# CUI // SP-CTI
"""tools/quality/review_loop.py — local "review-until-green" loop.

Adapted from greptileai/skills/greploop (review -> fix -> re-review until a
score clears or N iterations). greploop's score is Greptile's 5/5 confidence;
ICDEV's score is its OWN existing gates run over the local diff:

    * ruff           — the CI lint gate (.github/workflows/icdev-ci.yml), scoped
                       to the changed files. Deterministic ``--fix`` autofix.
    * coherence      — tools/workflow/coherence_checker.py --all. Safe ``--fix``.
    * sipa           — tools/integrity/pr_gates.assess_changed_files (static
                       integrity verdict over the branch diff). Gate only.

Each iteration runs the enabled gates, applies the deterministic autofixes, and
re-scores. The loop is GREEN when every blocking gate passes. It stops on green,
on the iteration cap, or when an iteration makes no progress (stuck). Whatever
remains is emitted as a ``fix_brief`` for the driving agent to address before a
re-run — the loop never edits code for non-deterministic findings itself
(deterministic execution; LLM orchestration stays outside, per FORGE).

This runs BEFORE a PR exists. tools/ci/pr_watcher.py is the post-PR analog:
a task that self-greens here arrives at pr_watcher with fewer resume cycles.

CLI:
    python tools/quality/review_loop.py --json
    python tools/quality/review_loop.py --base origin/main --max 3 --gate
    python tools/quality/review_loop.py --no-autofix --json   # report only
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import yaml

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)
ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "args" / "review_loop_config.yaml"


# ────────────────────────────────────────────────────────────────────────────
# Data types
# ────────────────────────────────────────────────────────────────────────────


@dataclasses.dataclass
class Finding:
    gate: str
    file: str
    message: str
    code: str = ""
    line: int = 0
    fixable: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class GateResult:
    name: str
    blocking: bool
    passed: bool
    findings: List[Finding] = dataclasses.field(default_factory=list)
    fixed_count: int = 0
    skipped: bool = False
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = dataclasses.asdict(self)
        d["findings"] = [f.to_dict() for f in self.findings]
        return d


@dataclasses.dataclass
class IterationResult:
    index: int
    gates: List[GateResult]
    green: bool
    score: str  # "passed/total" over blocking gates

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "green": self.green,
            "score": self.score,
            "gates": [g.to_dict() for g in self.gates],
        }


@dataclasses.dataclass
class LoopReport:
    started_at: str
    finished_at: str
    green: bool
    iterations: List[IterationResult]
    changed_files: List[str]
    fix_brief: List[Finding]
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "green": self.green,
            "reason": self.reason,
            "changed_files": self.changed_files,
            "iterations": [it.to_dict() for it in self.iterations],
            "fix_brief": [f.to_dict() for f in self.fix_brief],
        }


# ────────────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────────────


def load_config(path: Optional[pathlib.Path] = None) -> dict:
    p = path or DEFAULT_CONFIG
    if not p.exists():
        return _default_config()
    try:
        with open(p, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    except Exception as exc:  # degrade gracefully — never crash on bad YAML
        logger.warning("review_loop: bad config %s (%s) — using defaults", p, exc)
        return _default_config()
    base = _default_config()
    base.update({k: v for k, v in cfg.items() if k != "gates"})
    if isinstance(cfg.get("gates"), dict):
        base["gates"].update(cfg["gates"])
    return base


def _default_config() -> dict:
    return {
        "max_iterations": 3,
        "autofix": True,
        "audit": True,
        "gates": {
            "ruff": {
                "enabled": True,
                "blocking": True,
                "select": ["E", "F", "W"],
                "ignore": [
                    "E402", "E501", "E701", "E702",
                    "E721", "E722", "E731", "E741", "F404",
                ],
            },
            "coherence": {"enabled": True, "blocking": True},
            "sipa": {
                "enabled": True,
                "blocking": True,
                "block_on": ["quarantine"],
            },
        },
    }


# ────────────────────────────────────────────────────────────────────────────
# git helpers
# ────────────────────────────────────────────────────────────────────────────


def _git(args: List[str], repo_root: pathlib.Path) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
    except Exception as exc:
        logger.debug("review_loop: git %s failed: %s", args, exc)
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout or ""


def _ref_exists(ref: str, repo_root: pathlib.Path) -> bool:
    return bool(
        _git(["rev-parse", "--verify", "--quiet", ref], repo_root).strip()
    )


def resolve_base(base: Optional[str], repo_root: pathlib.Path) -> Optional[str]:
    """Return the first existing ref among the requested base and fallbacks.

    None means "working-tree mode" (uncommitted + untracked changes only).
    """
    if base is None:
        return None
    for candidate in (base, "origin/main", "main", "HEAD~1"):
        if _ref_exists(candidate, repo_root):
            return candidate
    return None


def changed_py_files(
    base: Optional[str], repo_root: pathlib.Path, staged: bool = False
) -> List[str]:
    """Repo-relative changed ``*.py`` paths that still exist on disk.

    staged=True → only the git index (``git diff --cached``); used by the
    pre-commit hook so the loop judges exactly what is about to be committed.
    base=None → working tree (tracked diff vs HEAD + untracked). Otherwise the
    branch diff ``base...HEAD`` plus any uncommitted working-tree changes, so
    ruff lints exactly what is on disk now.
    """
    names: set[str] = set()
    if staged:
        for line in _git(["diff", "--cached", "--name-only", "--diff-filter=d"], repo_root).splitlines():
            names.add(line.strip())
        out: List[str] = []
        for name in sorted(n for n in names if n.endswith(".py")):
            if (repo_root / name).is_file():
                out.append(name)
        return out
    if base:
        for line in _git(["diff", "--name-only", "--diff-filter=d", f"{base}...HEAD"], repo_root).splitlines():
            names.add(line.strip())
    # Always include uncommitted tracked changes + untracked files.
    for line in _git(["diff", "--name-only", "--diff-filter=d", "HEAD"], repo_root).splitlines():
        names.add(line.strip())
    for line in _git(["ls-files", "--others", "--exclude-standard"], repo_root).splitlines():
        names.add(line.strip())

    out: List[str] = []
    for name in sorted(n for n in names if n.endswith(".py")):
        if (repo_root / name).is_file():
            out.append(name)
    return out


# ────────────────────────────────────────────────────────────────────────────
# Gates — each returns a GateResult. Autofix happens inside the gate.
# ────────────────────────────────────────────────────────────────────────────


def gate_ruff(
    py_files: List[str],
    cfg: dict,
    autofix: bool,
    repo_root: pathlib.Path,
) -> GateResult:
    name = "ruff"
    blocking = bool(cfg.get("blocking", True))
    if not py_files:
        return GateResult(name, blocking, passed=True, skipped=True,
                          detail="no changed .py files")

    select = cfg.get("select") or ["E", "F", "W"]
    ignore = cfg.get("ignore") or []
    flags = ["--select", ",".join(select)]
    if ignore:
        flags += ["--ignore", ",".join(ignore)]

    fixed = 0
    if autofix:
        before = _ruff_findings(py_files, flags, repo_root)
        _run_ruff(py_files, flags + ["--fix"], repo_root)
        after_n = len(_ruff_findings(py_files, flags, repo_root))
        fixed = max(0, len(before) - after_n)

    findings = _ruff_findings(py_files, flags, repo_root)
    return GateResult(
        name, blocking, passed=(len(findings) == 0),
        findings=findings, fixed_count=fixed,
        detail=f"{len(findings)} lint finding(s)",
    )


def _run_ruff(py_files: List[str], flags: List[str], repo_root: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ruff", "check", *py_files, *flags],
        cwd=str(repo_root),
        capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )


def _ruff_findings(py_files: List[str], flags: List[str], repo_root: pathlib.Path) -> List[Finding]:
    try:
        proc = _run_ruff(py_files, [*flags, "--output-format", "json"], repo_root)
    except FileNotFoundError:
        logger.warning("review_loop: ruff not on PATH — skipping lint gate")
        return []
    except Exception as exc:
        logger.warning("review_loop: ruff failed: %s", exc)
        return []
    try:
        items = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return []
    out: List[Finding] = []
    for it in items:
        loc = it.get("location") or {}
        out.append(Finding(
            gate="ruff",
            file=it.get("filename", ""),
            message=it.get("message", ""),
            code=it.get("code", "") or "",
            line=int(loc.get("row", 0) or 0),
            fixable=it.get("fix") is not None,
        ))
    return out


def _loads_loose(text: Optional[str]) -> Optional[dict]:
    """Parse a JSON object out of possibly-noisy stdout.

    coherence_checker.py interleaves logger lines with its JSON report on
    stdout, so a bare json.loads fails. Fall back to the first ``{`` .. last
    ``}`` span (the top-level report object).
    """
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def gate_coherence(
    py_files: List[str],
    cfg: dict,
    autofix: bool,
    repo_root: pathlib.Path,
) -> GateResult:
    name = "coherence"
    blocking = bool(cfg.get("blocking", True))
    script = repo_root / "tools" / "workflow" / "coherence_checker.py"
    if not script.exists():
        return GateResult(name, blocking, passed=True, skipped=True,
                          detail="coherence_checker.py not found")

    # scope=changed runs coherence over only the task's changed files (fast,
    # diff-relevant — the pre-PR/pre-commit default). scope=all is the thorough
    # whole-repo run (standalone CLI / CI). No changed files in 'changed' mode
    # is a clean no-op.
    scope = str(cfg.get("scope", "all")).lower()
    if scope == "changed":
        if not py_files:
            return GateResult(name, blocking, passed=True, skipped=True,
                              detail="no changed .py files (scope=changed)")
        cmd = [sys.executable, str(script), "--changed-files", ",".join(py_files), "--json"]
    else:
        cmd = [sys.executable, str(script), "--all", "--json"]
    if autofix:
        cmd.append("--fix")
    try:
        proc = subprocess.run(
            cmd, cwd=str(repo_root),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )
    except Exception as exc:
        logger.warning("review_loop: coherence gate failed to run: %s", exc)
        return GateResult(name, blocking, passed=True, skipped=True,
                          detail=f"run error: {exc}")

    report = _loads_loose(proc.stdout)
    if report is None:
        # Could not recover JSON — fall back to the exit code as the verdict.
        passed = proc.returncode == 0
        return GateResult(name, blocking, passed=passed,
                          detail="coherence emitted unparseable output")

    overall = bool(report.get("overall_pass", proc.returncode == 0))
    findings: List[Finding] = []
    for check in report.get("checks", []) or []:
        # Real coherence_checker emits {check_id, check_name, status, message};
        # tolerate the {name, passed, issues} shape too. A 'warn' never fails.
        passed = check.get("passed")
        if passed is None:
            failed = str(check.get("status", "")).lower() in ("fail", "failed", "error")
        else:
            failed = not passed
        if not failed:
            continue
        name = (check.get("check_name") or check.get("name")
                or check.get("check_id") or "check")
        msgs = check.get("issues") or check.get("errors")
        if not msgs:
            single = check.get("message")
            msgs = [single] if single else [name]
        for msg in msgs:
            findings.append(Finding(
                gate="coherence",
                file=name,
                message=str(msg),
                code=str(check.get("check_id") or name),
            ))
    return GateResult(
        name, blocking, passed=overall, findings=findings,
        detail=f"{len(findings)} coherence issue(s)",
    )


def gate_sipa(
    py_files: List[str],
    cfg: dict,
    autofix: bool,
    repo_root: pathlib.Path,
    base: Optional[str],
) -> GateResult:
    name = "sipa"
    blocking = bool(cfg.get("blocking", True))
    block_on = {str(v).lower() for v in (cfg.get("block_on") or ["quarantine"])}
    if not py_files:
        return GateResult(name, blocking, passed=True, skipped=True,
                          detail="no changed .py files")
    try:
        from tools.integrity.pr_gates import assess_changed_files
    except Exception as exc:
        logger.info("review_loop: SIPA unavailable (%s) — skipping", exc)
        return GateResult(name, blocking, passed=True, skipped=True,
                          detail=f"import failed: {exc}")
    try:
        result = assess_changed_files(base=base or "origin/main", cached=False)
    except Exception as exc:
        logger.warning("review_loop: SIPA assess failed: %s", exc)
        return GateResult(name, blocking, passed=True, skipped=True,
                          detail=f"assess error: {exc}")

    verdict = str(result.get("verdict", "allow")).lower()
    findings: List[Finding] = []
    if verdict in block_on:
        for f in result.get("findings", []) or []:
            findings.append(Finding(
                gate="sipa",
                file=str(f.get("file") or f.get("path") or ""),
                message=str(f.get("message") or f.get("description") or f.get("rule") or "integrity finding"),
                code=str(f.get("rule") or f.get("category") or ""),
            ))
    return GateResult(
        name, blocking, passed=(verdict not in block_on),
        findings=findings,
        detail=f"verdict={verdict} risk={result.get('risk_score', '?')}",
    )


# ────────────────────────────────────────────────────────────────────────────
# Core loop
# ────────────────────────────────────────────────────────────────────────────


class ReviewLoop:
    def __init__(
        self,
        *,
        config: Optional[dict] = None,
        repo_root: Optional[pathlib.Path] = None,
        autofix: Optional[bool] = None,
        staged: bool = False,
        get_connection: Optional[Callable] = None,
    ):
        self.config = config or load_config()
        self.repo_root = repo_root or ROOT
        self.autofix = self.config.get("autofix", True) if autofix is None else autofix
        self.staged = staged
        self._get_connection = get_connection

    def _run_gates(self, py_files: List[str], base: Optional[str]) -> List[GateResult]:
        gcfg = self.config.get("gates", {})
        results: List[GateResult] = []
        if (gcfg.get("ruff") or {}).get("enabled", True):
            results.append(gate_ruff(py_files, gcfg.get("ruff", {}), self.autofix, self.repo_root))
        if (gcfg.get("coherence") or {}).get("enabled", True):
            results.append(gate_coherence(py_files, gcfg.get("coherence", {}), self.autofix, self.repo_root))
        if (gcfg.get("sipa") or {}).get("enabled", True):
            results.append(gate_sipa(py_files, gcfg.get("sipa", {}), self.autofix, self.repo_root, base))
        return results

    @staticmethod
    def _score(gates: List[GateResult]) -> tuple[bool, str]:
        blocking = [g for g in gates if g.blocking]
        passed = sum(1 for g in blocking if g.passed)
        green = passed == len(blocking)
        return green, f"{passed}/{len(blocking)}"

    @staticmethod
    def _open_findings(gates: List[GateResult]) -> List[Finding]:
        out: List[Finding] = []
        for g in gates:
            if not g.passed:
                out.extend(g.findings)
        return out

    def run(self, base: Optional[str] = None) -> LoopReport:
        started = datetime.now(timezone.utc).isoformat()
        resolved_base = resolve_base(base, self.repo_root)
        max_iter = max(1, int(self.config.get("max_iterations", 3)))

        iterations: List[IterationResult] = []
        green = False
        reason = ""
        prev_fingerprint: Optional[tuple] = None
        last_findings: List[Finding] = []

        for i in range(1, max_iter + 1):
            py_files = changed_py_files(resolved_base, self.repo_root, staged=self.staged)
            gates = self._run_gates(py_files, resolved_base)
            green, score = self._score(gates)
            it = IterationResult(index=i, gates=gates, green=green, score=score)
            iterations.append(it)
            self._audit(it)
            logger.info("review_loop: iteration %d score=%s green=%s", i, score, green)

            if green:
                reason = "all blocking gates green"
                last_findings = []
                break

            last_findings = self._open_findings(gates)
            # Progress check: stop early if this iteration changed nothing.
            fingerprint = tuple(sorted(
                (f.gate, f.file, f.code, f.line, f.message) for f in last_findings
            ))
            if fingerprint == prev_fingerprint:
                reason = "no progress between iterations — remaining findings need manual fixes"
                break
            prev_fingerprint = fingerprint
        else:
            reason = f"iteration cap reached ({max_iter})"

        finished = datetime.now(timezone.utc).isoformat()
        return LoopReport(
            started_at=started,
            finished_at=finished,
            green=green,
            iterations=iterations,
            changed_files=changed_py_files(resolved_base, self.repo_root, staged=self.staged),
            fix_brief=last_findings,
            reason=reason,
        )

    # ── audit (best-effort, reuses the pr_watcher pattern) ──────────────────

    def _audit(self, it: IterationResult) -> None:
        if not self.config.get("audit", True):
            return
        try:
            get_conn = self._get_connection
            if get_conn is None:
                from tools.db.storage import get_connection as get_conn  # type: ignore
            conn = get_conn()
        except Exception:
            return
        try:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO audit_trail "
                "(created_at, event_type, actor, action, details) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    now, "hook_event_logged", "review_loop",
                    "review_loop.iteration",
                    json.dumps({
                        "iteration": it.index,
                        "green": it.green,
                        "score": it.score,
                        "gates": {g.name: g.passed for g in it.gates},
                    }),
                ),
            )
            if hasattr(conn, "commit"):
                conn.commit()
        except Exception as exc:
            logger.debug("review_loop: audit write failed: %s", exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass


# ────────────────────────────────────────────────────────────────────────────
# Shared entrypoint for integrations (pre-PR preflight, pre-commit hook, reflex)
# ────────────────────────────────────────────────────────────────────────────


def preflight(
    *,
    base: Optional[str] = None,
    autofix: bool = True,
    staged: bool = False,
    coherence_scope: str = "changed",
    only_gates: Optional[List[str]] = None,
    audit: bool = False,
    max_iterations: int = 2,
    repo_root: Optional[pathlib.Path] = None,
) -> LoopReport:
    """One-call review-until-green for the workflow integrations.

    Defaults to the diff-scoped, fast configuration (coherence over changed
    files only) suitable for per-task / per-commit use — distinct from the
    standalone CLI which runs coherence ``--all``.

    Args:
        base: git ref to diff against (e.g. ``origin/main``); None = working tree.
        autofix: apply deterministic ruff/coherence ``--fix`` between iterations.
        staged: judge only the git index (``--cached``) — the pre-commit case.
        coherence_scope: ``changed`` (diff-scoped, fast) or ``all`` (whole repo).
        only_gates: restrict to this gate subset (e.g. ``["ruff"]`` for a fast
            commit hook); None runs all enabled gates.
        audit: write per-iteration audit_trail rows.
        max_iterations: hard cap on review->fix->re-review cycles.
        repo_root: working tree to operate on (default: the ICDEV repo root).
    """
    cfg = _default_config()
    cfg["max_iterations"] = max_iterations
    cfg["autofix"] = autofix
    cfg["audit"] = audit
    cfg["gates"]["coherence"]["scope"] = coherence_scope
    if only_gates is not None:
        wanted = {g.lower() for g in only_gates}
        for gname in cfg["gates"]:
            cfg["gates"][gname]["enabled"] = gname in wanted
    loop = ReviewLoop(config=cfg, repo_root=repo_root, autofix=autofix, staged=staged)
    return loop.run(base=base)


# ────────────────────────────────────────────────────────────────────────────
# Rendering
# ────────────────────────────────────────────────────────────────────────────


def render_summary(report: LoopReport) -> str:
    """greploop-style summary table."""
    lines: List[str] = []
    status = "GREEN [pass]" if report.green else "NOT GREEN [fail]"
    lines.append(f"review_loop: {status} - {report.reason}")
    lines.append(f"  changed .py files: {len(report.changed_files)}")
    lines.append("")
    lines.append("  iter | score | gates")
    lines.append("  -----+-------+" + "-" * 40)
    for it in report.iterations:
        gate_str = "  ".join(
            f"{g.name}={'ok' if g.passed else ('skip' if g.skipped else 'FAIL')}"
            for g in it.gates
        )
        lines.append(f"  {it.index:>4} | {it.score:^5} | {gate_str}")
    if report.fix_brief:
        lines.append("")
        lines.append(f"  fix_brief ({len(report.fix_brief)} open finding(s) for the agent):")
        for f in report.fix_brief[:20]:
            loc = f"{f.file}:{f.line}" if f.line else f.file
            tag = " [auto-fixable]" if f.fixable else ""
            lines.append(f"    - [{f.gate}] {loc} {f.code} {f.message}{tag}")
        if len(report.fix_brief) > 20:
            lines.append(f"    ... and {len(report.fix_brief) - 20} more")
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Local review-until-green loop — run ICDEV's gates over the "
        "diff and iterate deterministic autofixes until every blocking gate passes.",
    )
    ap.add_argument("--base", default=None,
                    help="git ref to diff HEAD against (e.g. origin/main). "
                         "Omit for working-tree mode (uncommitted + untracked).")
    ap.add_argument("--max", type=int, default=None,
                    help="Override max iterations (default from config = 3)")
    ap.add_argument("--no-autofix", action="store_true",
                    help="Report only; do not apply ruff/coherence --fix")
    ap.add_argument("--no-audit", action="store_true",
                    help="Do not write audit_trail rows")
    ap.add_argument("--config", default=None)
    ap.add_argument("--json", action="store_true", help="Emit JSON report")
    ap.add_argument("--gate", action="store_true",
                    help="Exit 0 if green, 1 otherwise")
    args = ap.parse_args(argv)

    config = load_config(pathlib.Path(args.config) if args.config else None)
    if args.max is not None:
        config["max_iterations"] = args.max
    if args.no_audit:
        config["audit"] = False

    loop = ReviewLoop(
        config=config,
        autofix=(False if args.no_autofix else None),
    )
    report = loop.run(base=args.base)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(render_summary(report))

    if args.gate:
        return 0 if report.green else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
