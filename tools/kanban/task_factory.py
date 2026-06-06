# CUI // SP-CTI
"""Kanban task factory — the single, validated path for creating Kanban tasks.

Every seeder (and any other code that queues work for the autonomous Kanban
board) should create tasks through :func:`create_tasks` instead of hand-rolling
``INSERT INTO kanban_tasks``. This guarantees:

* **No deadlock state.** Status defaults to ``backlog`` (dep-gated, dispatched
  ASAP). A ``scheduled`` task without a ``scheduled_at`` is impossible to
  construct here — it raises (strict) or is coerced to ``backlog`` — because the
  dispatcher's scheduled query requires ``scheduled_at IS NOT NULL`` and such a
  row would never dispatch (see plan serialized-squishing-dawn,
  [[kanban-autonomous-seeding-workflow]]).
* **Quality.** The batch is validated through ``seed_validator.validate_batch``
  (structural + content + LLM rubric) before any write when ``strict=True``.
* **Correct deps.** Writes both the scalar ``depends_on_task_id`` (read by the
  scheduler for cycle detection / gating) and the ``kanban_task_deps`` junction
  rows (read by the board UI for multi-parent dep graphs).
* **Idempotency.** Existing ids are skipped, never duplicated.

Typical use from a seeder::

    from tools.kanban.task_factory import TaskSpec, create_tasks
    specs = [TaskSpec(id="cwk-db-01", title="...", description="...",
                      task_type="build", priority="high",
                      depends_on_task_id="cwk-loader-01"), ...]
    report = create_tasks("cwk", specs)
    print(report.summary())
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from tools.db.storage import get_connection
from tools.kanban.seed_validator import ValidationReport, validate_batch


class SeedValidationError(ValueError):
    """Raised in strict mode when a batch fails validation."""

    def __init__(self, report: ValidationReport):
        self.report = report
        super().__init__("Kanban seed batch failed validation:\n" + report.scorecard())


@dataclass
class TaskSpec:
    """One Kanban task to create. ``status`` defaults to backlog on purpose."""
    id: str
    title: str
    description: str
    task_type: str = "build"
    priority: str = "medium"
    depends_on_task_id: Optional[str] = None          # scalar parent (scheduler gate)
    depends_on: List[str] = field(default_factory=list)  # extra junction parents
    status: str = "backlog"
    scheduled_at: Optional[str] = None                # required iff status == 'scheduled'
    classification: str = "CUI // SP-CTI"
    project_id: Optional[str] = None                  # defaults to project_key
    tags: Optional[List[str]] = None
    dispatch_source: Optional[str] = None             # optional; preserved if column exists
    executor_type: Optional[str] = None               # optional; else DB default


@dataclass
class SeedReport:
    project_key: str
    seeded: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)   # already existed
    healed: List[str] = field(default_factory=list)     # scheduled+NULL -> backlog (non-strict)
    warnings: List[str] = field(default_factory=list)
    validation: Optional[ValidationReport] = None
    dry_run: bool = False

    def summary(self) -> str:
        lines = [
            f"Seed '{self.project_key}': "
            f"{len(self.seeded)} created, {len(self.skipped)} skipped"
            + (f", {len(self.healed)} healed" if self.healed else "")
            + (" (dry-run)" if self.dry_run else ""),
        ]
        if self.healed:
            lines.append(f"  healed scheduled->backlog: {self.healed}")
        for w in self.warnings:
            lines.append(f"  ! {w}")
        return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(spec: Union[TaskSpec, dict], project_key: str) -> Dict[str, Any]:
    """Coerce a TaskSpec/dict to a fully-populated row dict with safe defaults."""
    if isinstance(spec, TaskSpec):
        d = {
            "id": spec.id, "title": spec.title, "description": spec.description,
            "task_type": spec.task_type, "priority": spec.priority,
            "depends_on_task_id": spec.depends_on_task_id,
            "depends_on": list(spec.depends_on or []),
            "status": spec.status, "scheduled_at": spec.scheduled_at,
            "classification": spec.classification,
            "project_id": spec.project_id, "tags": spec.tags,
            "dispatch_source": spec.dispatch_source,
            "executor_type": spec.executor_type,
        }
    else:
        d = dict(spec)
    d.setdefault("task_type", "build")
    d.setdefault("priority", "medium")
    d.setdefault("status", "backlog")
    d.setdefault("classification", "CUI // SP-CTI")
    d.setdefault("depends_on", [])
    d["project_id"] = d.get("project_id") or project_key
    return d


def _coerce_status(d: Dict[str, Any], *, strict: bool, report: SeedReport) -> None:
    """Enforce the no-deadlock invariant: scheduled requires scheduled_at."""
    if (d.get("status") or "").lower() == "scheduled" and not d.get("scheduled_at"):
        if strict:
            # Surfaced by validate_batch as a structural error; this is a backstop.
            return
        d["status"] = "backlog"
        report.healed.append(d["id"])
        report.warnings.append(
            f"{d['id']}: status 'scheduled' had no scheduled_at -> coerced to 'backlog'"
        )


def create_tasks(
    project_key: str,
    tasks: List[Union[TaskSpec, dict]],
    *,
    conn: Any = None,
    dry_run: bool = False,
    strict: bool = True,
    register_project: bool = True,
    llm_grade: bool = True,
) -> SeedReport:
    """Create Kanban tasks through the validated, deadlock-safe path.

    Args:
        project_key: project prefix / project_id (e.g. ``"cwk"``).
        tasks: list of :class:`TaskSpec` or dicts.
        conn: optional open connection (else one is opened/closed here).
        dry_run: validate + report but write nothing.
        strict: validate the batch first and raise :class:`SeedValidationError`
            on any failure (recommended). When ``False``, validation is skipped
            and scheduled+NULL rows are silently coerced to backlog.
        register_project: call ``kanban_project_sync.sync_projects()`` afterwards.
        llm_grade: run the LLM rubric during strict validation.

    Returns:
        :class:`SeedReport`.
    """
    report = SeedReport(project_key=project_key, dry_run=dry_run)
    norm = [_normalize(t, project_key) for t in tasks]

    own_conn = conn is None
    _conn = conn or get_connection()
    try:
        # ---- validate (strict) ------------------------------------------- #
        if strict:
            vr = validate_batch(project_key, norm, llm_grade=llm_grade, conn=_conn)
            report.validation = vr
            if not vr.ok:
                raise SeedValidationError(vr)
        else:
            for d in norm:
                _coerce_status(d, strict=False, report=report)

        if dry_run:
            return report

        # ---- write ------------------------------------------------------- #
        now = _now()
        dep_table_ok = _has_dep_table(_conn)
        avail = _table_columns(_conn)  # schema-tolerant across PG / SQLite variants
        for d in norm:
            tid = d["id"]
            existing = _conn.execute(
                "SELECT id FROM kanban_tasks WHERE id = ?", (tid,)
            ).fetchone()
            if existing:
                report.skipped.append(tid)
                continue
            # Always-write columns (core), then optional columns only when the
            # column exists AND a value is given (so we never clobber a DB
            # default like executor_type='claude_cli' with NULL).
            core = {
                "id": tid, "title": d["title"], "description": d.get("description"),
                "task_type": d["task_type"], "priority": d["priority"],
                "status": d["status"], "scheduled_at": d.get("scheduled_at"),
                "created_at": now, "updated_at": now,
                "depends_on_task_id": d.get("depends_on_task_id"),
                "project_id": d.get("project_id"), "classification": d.get("classification"),
            }
            optional = {
                "tags": _serialize_tags(d.get("tags")),
                "dispatch_source": d.get("dispatch_source"),
                "executor_type": d.get("executor_type"),
            }
            cols, vals = [], []
            for c, v in core.items():
                if c in avail:
                    cols.append(c); vals.append(v)
            for c, v in optional.items():
                if c in avail and v is not None:
                    cols.append(c); vals.append(v)
            placeholders = ", ".join("?" for _ in cols)
            _conn.execute(
                f"INSERT INTO kanban_tasks ({', '.join(cols)}) VALUES ({placeholders})",  # nosec B608
                tuple(vals),
            )
            # junction rows: scalar parent + any extra depends_on
            if dep_table_ok:
                parents = list(d.get("depends_on") or [])
                if d.get("depends_on_task_id"):
                    parents.append(d["depends_on_task_id"])
                for parent in dict.fromkeys(parents):  # de-dup, preserve order
                    _conn.execute(
                        "INSERT INTO kanban_task_deps (task_id, depends_on_id, created_at) "
                        "VALUES (?, ?, ?) ON CONFLICT (task_id, depends_on_id) DO NOTHING",
                        (tid, parent, now),
                    )
            report.seeded.append(tid)

        _conn.commit()
    finally:
        if own_conn:
            try:
                _conn.close()
            except Exception:
                pass

    # ---- register project (best-effort) ---------------------------------- #
    if register_project and not dry_run and report.seeded:
        try:
            from tools.project.kanban_project_sync import sync_projects
            sync_projects()
        except Exception as exc:  # never fail a seed because sync hiccupped
            report.warnings.append(f"project sync skipped: {exc}")

    return report


def _has_dep_table(conn: Any) -> bool:
    try:
        conn.execute("SELECT 1 FROM kanban_task_deps LIMIT 1")
        return True
    except Exception:
        return False


def _table_columns(conn: Any) -> set:
    """Available kanban_tasks column names (works on PG + SQLite)."""
    try:
        cur = conn.execute("SELECT * FROM kanban_tasks LIMIT 0")
        return {d[0] for d in cur.description}
    except Exception:
        # Conservative core set if introspection fails.
        return {
            "id", "title", "description", "task_type", "priority", "status",
            "scheduled_at", "created_at", "updated_at", "depends_on_task_id",
        }


def _serialize_tags(tags: Any) -> Any:
    """tags column is TEXT — store a list as JSON, pass through str/None."""
    if tags is None or isinstance(tags, str):
        return tags
    try:
        import json
        return json.dumps(tags)
    except Exception:
        return str(tags)
