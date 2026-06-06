# CUI // SP-CTI
"""Kanban seed validator — structural + content + LLM-rubric quality gate.

Single source of truth for "is this batch of Kanban tasks safe and high-quality
enough to seed?" Used by ``tools/kanban/task_factory.py`` (in strict mode, before
any write) and runnable standalone as a pre-seed gate:

    python tools/kanban/seed_validator.py --gate --file spec.json
    python tools/kanban/seed_validator.py --gate --project cwk        # validate live DB rows

Three independent checks per task:
  1. Structural  — id convention, deps resolve, no cycles, status legal, scheduled⇒scheduled_at.
  2. Content     — deterministic: min length, names file paths, acceptance criteria, test plan.
  3. LLM rubric  — Haiku scores clarity/specificity/actionability 0-100; must clear threshold.

The LLM rubric degrades gracefully: if no provider is reachable (air-gap), it is
skipped and the deterministic gate alone decides. See plan
serialized-squishing-dawn and [[kanban-autonomous-seeding-workflow]].
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- #
# Canonical vocabularies (kept in sync with tools/kanban/state_machine.py)
# --------------------------------------------------------------------------- #
# Mirror the DB CHECK constraints on kanban_tasks (authoritative). Keep in sync
# if the constraints change (tools/db/init_icdev_db.py + migrations).
VALID_STATUSES = {
    "backlog", "scheduled", "in_progress", "done", "token_exhausted",
    "suggested", "decomposed", "validating", "needs_decomposition",
}
VALID_PRIORITIES = {"critical", "high", "medium", "low"}
VALID_TASK_TYPES = {"build", "run", "fix", "research", "deploy", "test", "chore"}

# id like  cwk-ref-01 / acf-db-04 / dt-iqe-11  — <prefix>(-<epic>)+-<NN>
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+-\d+$")
PATH_RE = re.compile(r"\b[\w./-]+\.(?:py|md|html|js|ts|jsx|tsx|yaml|yml|sql|json|css|sh)\b")
DIR_RE = re.compile(r"\b(?:tools|icdev|tests|args|docs|context|goals|features)/[\w./-]+")
ACCEPT_RE = re.compile(r"accept|done when|success criteria|definition of done|acceptance", re.I)
TEST_RE = re.compile(r"\btest|pytest|verif|e2e|behave|assert|smoke", re.I)

MIN_DESC_LEN = 200
RUBRIC_THRESHOLD = 70


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #
@dataclass
class TaskFinding:
    task_id: str
    struct_errors: List[str] = field(default_factory=list)
    content_errors: List[str] = field(default_factory=list)
    rubric_score: Optional[int] = None        # None == not graded
    rubric_reason: str = ""
    warnings: List[str] = field(default_factory=list)

    @property
    def struct_ok(self) -> bool:
        return not self.struct_errors

    @property
    def content_ok(self) -> bool:
        return not self.content_errors

    @property
    def rubric_ok(self) -> bool:
        return self.rubric_score is None or self.rubric_score >= RUBRIC_THRESHOLD

    @property
    def ok(self) -> bool:
        return self.struct_ok and self.content_ok and self.rubric_ok


@dataclass
class ValidationReport:
    project_key: str
    findings: List[TaskFinding] = field(default_factory=list)
    batch_errors: List[str] = field(default_factory=list)   # cycles, dup ids, collisions
    llm_used: bool = False

    @property
    def ok(self) -> bool:
        return not self.batch_errors and all(f.ok for f in self.findings)

    def failures(self) -> List[TaskFinding]:
        return [f for f in self.findings if not f.ok]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_key": self.project_key,
            "ok": self.ok,
            "llm_used": self.llm_used,
            "batch_errors": self.batch_errors,
            "tasks": [
                {
                    "id": f.task_id,
                    "ok": f.ok,
                    "struct_errors": f.struct_errors,
                    "content_errors": f.content_errors,
                    "rubric_score": f.rubric_score,
                    "rubric_reason": f.rubric_reason,
                    "warnings": f.warnings,
                }
                for f in self.findings
            ],
        }

    def scorecard(self) -> str:
        """Human-readable scorecard table."""
        lines = [
            f"Seed validation — project '{self.project_key}'  "
            f"({'PASS' if self.ok else 'FAIL'})",
            f"  LLM rubric: {'on' if self.llm_used else 'off (deterministic-only)'}",
        ]
        if self.batch_errors:
            lines.append("  Batch errors:")
            lines += [f"    ✗ {e}" for e in self.batch_errors]
        lines.append(f"  {'ID':<26} {'STRUCT':<7} {'CONTENT':<8} {'RUBRIC':<7} STATUS")
        for f in self.findings:
            rub = "-" if f.rubric_score is None else str(f.rubric_score)
            lines.append(
                f"  {f.task_id:<26} "
                f"{'ok' if f.struct_ok else 'FAIL':<7} "
                f"{'ok' if f.content_ok else 'FAIL':<8} "
                f"{rub:<7} "
                f"{'PASS' if f.ok else 'FAIL'}"
            )
            for e in f.struct_errors + f.content_errors:
                lines.append(f"      ✗ {e}")
            if not f.rubric_ok:
                lines.append(f"      ✗ rubric {f.rubric_score} < {RUBRIC_THRESHOLD}: {f.rubric_reason}")
            for w in f.warnings:
                lines.append(f"      ! {w}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #
def _as_dict(task: Any) -> Dict[str, Any]:
    """Accept a TaskSpec, dataclass, or plain dict; return a plain dict."""
    if isinstance(task, dict):
        return dict(task)
    # dataclass / object with attributes
    out = {}
    for attr in (
        "id", "title", "description", "task_type", "priority", "status",
        "scheduled_at", "depends_on_task_id", "depends_on", "classification",
        "project_id", "tags",
    ):
        if hasattr(task, attr):
            out[attr] = getattr(task, attr)
    return out


def _deps_of(t: Dict[str, Any]) -> List[str]:
    """All declared dependency ids (scalar + junction)."""
    deps: List[str] = []
    scalar = t.get("depends_on_task_id")
    if scalar:
        deps.append(scalar)
    deps += list(t.get("depends_on") or [])
    return deps


# --------------------------------------------------------------------------- #
# Structural checks
# --------------------------------------------------------------------------- #
def _check_structural(t: Dict[str, Any], known_ids: set, project_key: str) -> List[str]:
    errors: List[str] = []
    tid = t.get("id") or ""
    if not tid:
        return ["missing id"]
    if not ID_RE.match(tid):
        errors.append(f"id '{tid}' does not match <prefix>(-<epic>)+-<NN> convention")
    if project_key and not tid.startswith(project_key):
        errors.append(f"id '{tid}' does not start with project prefix '{project_key}'")
    if not (t.get("title") or "").strip():
        errors.append("missing title")

    status = (t.get("status") or "backlog").lower()
    if status not in VALID_STATUSES:
        errors.append(f"status '{status}' not in {sorted(VALID_STATUSES)}")
    if status == "scheduled" and not t.get("scheduled_at"):
        errors.append(
            "status='scheduled' requires a scheduled_at timestamp "
            "(use 'backlog' for dep-gated work — scheduled+NULL deadlocks the dispatcher)"
        )

    prio = (t.get("priority") or "medium").lower()
    if prio not in VALID_PRIORITIES:
        errors.append(f"priority '{prio}' not in {sorted(VALID_PRIORITIES)}")

    ttype = (t.get("task_type") or "build").lower()
    if ttype not in VALID_TASK_TYPES:
        errors.append(f"task_type '{ttype}' not in {sorted(VALID_TASK_TYPES)}")

    for dep in _deps_of(t):
        if dep == tid:
            errors.append(f"task depends on itself ({tid})")
        elif dep not in known_ids:
            errors.append(f"depends on unknown task '{dep}' (not in batch or DB)")
    return errors


def _check_content(desc: str) -> List[str]:
    errors: List[str] = []
    desc = desc or ""
    if len(desc) < MIN_DESC_LEN:
        errors.append(f"description too thin ({len(desc)} chars < {MIN_DESC_LEN})")
    if not (PATH_RE.search(desc) or DIR_RE.search(desc)):
        errors.append("description names no file/dir path (point the worker at concrete files)")
    if not ACCEPT_RE.search(desc):
        errors.append("description has no acceptance criteria ('acceptance' / 'done when' / 'success criteria')")
    if not TEST_RE.search(desc):
        errors.append("description has no test/verification plan ('test' / 'pytest' / 'verify' / 'e2e')")
    return errors


def _detect_cycles(tasks: List[Dict[str, Any]]) -> List[str]:
    """Topological sort over scalar+junction edges; report cycles + dup ids."""
    errors: List[str] = []
    ids = [t.get("id") for t in tasks if t.get("id")]
    dups = {i for i in ids if ids.count(i) > 1}
    if dups:
        errors.append(f"duplicate task ids in batch: {sorted(dups)}")

    graph = {t["id"]: set(_deps_of(t)) for t in tasks if t.get("id")}
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}

    def visit(node: str, stack: List[str]) -> bool:
        color[node] = GREY
        for dep in graph.get(node, ()):
            if dep not in graph:        # dep outside batch (in DB) — not part of a batch cycle
                continue
            if color[dep] == GREY:
                errors.append(f"dependency cycle: {' -> '.join(stack + [node, dep])}")
                return True
            if color[dep] == WHITE and visit(dep, stack + [node]):
                return True
        color[node] = BLACK
        return False

    for n in graph:
        if color[n] == WHITE:
            if visit(n, []):
                break
    return errors


# --------------------------------------------------------------------------- #
# LLM rubric (optional, degrades to no-op offline)
# --------------------------------------------------------------------------- #
_RUBRIC_SYSTEM = (
    "You are a strict engineering reviewer grading the quality of a single "
    "autonomous-build task description. A downstream AI agent receives ONLY this "
    "description and must implement the task without asking questions. Grade how "
    "clear, specific, and actionable it is. Reward: concrete file paths, reuse "
    "pointers, explicit acceptance criteria, and a test/verification plan. "
    "Penalize: vagueness, missing context, no success criteria. "
    "Respond ONLY with JSON: {\"score\": <0-100 int>, \"reason\": \"<one sentence>\"}."
)


def _grade_with_llm(task: Dict[str, Any]) -> Optional[tuple]:
    """Return (score:int, reason:str) or None if LLM is unavailable."""
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
    except Exception:
        return None
    try:
        router = LLMRouter()
        user = (
            f"Task id: {task.get('id')}\n"
            f"Title: {task.get('title')}\n"
            f"Type: {task.get('task_type')}  Priority: {task.get('priority')}\n\n"
            f"Description:\n{task.get('description', '')}"
        )
        req = LLMRequest(
            messages=[{"role": "user", "content": [{"type": "text", "text": user}]}],
            system_prompt=_RUBRIC_SYSTEM,
            effort="low",
            max_tokens=300,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = router.invoke("code_generation", req)
        data = resp.structured_output
        if not data:
            content = (resp.content or "").strip()
            if content.startswith("```"):
                content = "\n".join(
                    ln for ln in content.split("\n") if not ln.startswith("```")
                )
            data = json.loads(content)
        score = int(data.get("score"))
        return max(0, min(100, score)), str(data.get("reason", ""))[:200]
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def validate_batch(
    project_key: str,
    tasks: List[Any],
    *,
    llm_grade: bool = True,
    conn: Any = None,
) -> ValidationReport:
    """Validate a batch of task specs/dicts. Pure read — never writes."""
    norm = [_as_dict(t) for t in tasks]
    report = ValidationReport(project_key=project_key)

    # Build the set of ids deps may resolve against: this batch + (optionally) DB.
    known_ids = {t.get("id") for t in norm if t.get("id")}
    if conn is not None:
        try:
            rows = conn.execute("SELECT id FROM kanban_tasks").fetchall()
            for r in rows:
                known_ids.add(r["id"] if isinstance(r, dict) or hasattr(r, "keys") else r[0])
        except Exception:
            pass  # DB unavailable — resolve within batch only

    report.batch_errors = _detect_cycles(norm)

    do_llm = llm_grade
    for t in norm:
        finding = TaskFinding(task_id=t.get("id") or "<no-id>")
        finding.struct_errors = _check_structural(t, known_ids, project_key)
        finding.content_errors = _check_content(t.get("description", ""))
        # Only spend LLM tokens on structurally+content-valid tasks.
        if do_llm and finding.struct_ok and finding.content_ok:
            graded = _grade_with_llm(t)
            if graded is None:
                do_llm = False  # provider unreachable — stop trying, degrade whole batch
                finding.warnings.append("LLM rubric unavailable — deterministic checks only")
            else:
                finding.rubric_score, finding.rubric_reason = graded
        report.findings.append(finding)

    report.llm_used = llm_grade and do_llm
    return report


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _load_from_file(path: str) -> tuple:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        return data.get("project_key", ""), data.get("tasks", [])
    return "", data  # bare list


def _load_from_db(project_key: str) -> List[Dict[str, Any]]:
    from tools.db.storage import get_connection
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, description, task_type, priority, status, "
            "scheduled_at, depends_on_task_id FROM kanban_tasks WHERE project_id = ?",
            (project_key,),
        ).fetchall()
    return [dict(r) for r in rows]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Validate a Kanban task seed batch.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="JSON spec: {project_key, tasks:[...]} or a bare list")
    src.add_argument("--project", help="Validate live DB rows for this project_id")
    ap.add_argument("--gate", action="store_true", help="Exit non-zero on any failure")
    ap.add_argument("--no-llm", action="store_true", help="Skip the LLM rubric")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of a scorecard")
    args = ap.parse_args(argv)

    conn = None
    if args.file:
        project_key, tasks = _load_from_file(args.file)
    else:
        project_key, tasks = args.project, _load_from_db(args.project)

    report = validate_batch(project_key, tasks, llm_grade=not args.no_llm, conn=conn)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.scorecard())
    if args.gate and not report.ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
