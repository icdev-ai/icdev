# CUI // SP-CTI
"""Keep operating policy in kanban task descriptions in sync with the card.

THE BUG THIS EXISTS FOR
=======================
Operating policy is COPIED into every task description at seed time, so
changing the policy does not change the tasks that already exist. On
2026-08-08 the HGX card and ``tools/kanban/seed_hgx_kanban.py`` were both
corrected to say "open PRs normally, not --draft"; 35 of 38 hgx rows already
existed and kept the ORIGINAL sentence. A dispatched session reads its TASK
DESCRIPTION, not the card, so every one of them kept opening drafts —
``pr_watcher`` may not merge a draft, so finished green work piled up and
needed a human three separate times (8 PRs, then 7, then 5).

WHAT THIS MODULE DOES
=====================
Two mechanisms, in precedence order, both driven by ONE source of truth: the
``policy:`` field on the project entry in ``args/projects.yaml``.

1. **Marked block (the steady state).** A description may carry a delimited
   region::

       <!-- icdev:policy hgx -->
       ...the card's current policy text...
       <!-- /icdev:policy -->

   The body is compared against the card on every scan and rewritten when they
   disagree. No pattern matching, no rules table, exact and idempotent — once a
   row carries a block it tracks the card forever.

2. **Legacy pattern (the one-time migration).** The 3000+ rows that already
   exist carry policy as unmarked free text. A rule in
   ``args/kanban_policy_drift.yaml`` names the stale phrasing with a regex and,
   on ``--fix``, replaces the matched span with a marked block. After that the
   row is in case 1 and the rule is never needed for it again.

So the rules table only has to describe what is STALE, never what is current —
the current text always comes from the card. That is the honest limit of a
drift detector (see ``docs/features/kax-merge-02-policy-drift.md`` for why the
alternatives lose), narrowed as far as it can be narrowed.

FAIL-CLOSED SCOPING
===================
``applies_to.task_id_prefix`` is REQUIRED and must be non-empty. A rule that
does not name the projects it governs matches nothing and is rejected at load.
``exempt_projects`` is a second, explicit veto that outranks everything,
including a ``policy:`` field: AGOV is MANUAL-ONLY and its 19 rows legitimately
say ``--draft``, so ``agov`` is listed there and no mechanism in this module can
touch an ``agov-`` row.

SAFETY
======
* Dry-run is the default. ``--fix`` is required to write.
* ``--fix`` defaults to rows a session could still be handed
  (``backlog``/``scheduled``). ``done`` and ``in_progress`` are reported but not
  rewritten — a done row is archaeology, and rewriting an ``in_progress`` row
  would clobber a live session's own edits to its description.
* Every rewrite appends a ``kanban_task_comments`` row recording the rule, the
  action and the text that was replaced, so the change is visible on the board
  and not only in a log line.

Usage::

    python tools/kanban/policy_drift.py --json              # scan, no writes
    python tools/kanban/policy_drift.py --fix --json        # rewrite dispatchable rows
    python tools/kanban/policy_drift.py --fix --statuses backlog,scheduled,in_progress
    python tools/kanban/policy_drift.py --project hgx --json
"""
from __future__ import annotations

import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

import argparse  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import uuid  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple  # noqa: E402

logger = get_logger(__name__)

def _resolve_config(name: str) -> Path:
    """Find ``args/<name>`` from either tree.

    ``_REPO_ROOT`` is the repo root under ``tools/`` and ``<repo>/icdev`` under
    the mirror — and ``icdev/args/`` carries 22 of the 321 files in ``args/``,
    so the mirrored copy of this module cannot assume its own sibling exists.
    Prefer the local one (a wheel install has only that), fall back to the
    parent, and name the local path when neither is there.
    """
    local = _REPO_ROOT / "args" / name
    if local.is_file():
        return local
    parent = _REPO_ROOT.parent / "args" / name
    return parent if parent.is_file() else local


PROJECTS_YAML = _resolve_config("projects.yaml")
RULES_YAML = _resolve_config("kanban_policy_drift.yaml")

#: Marker pair that delimits a card-linked policy block inside a description.
BLOCK_OPEN = "<!-- icdev:policy {key} -->"
BLOCK_CLOSE = "<!-- /icdev:policy -->"

_BLOCK_RE = re.compile(
    r"<!--\s*icdev:policy\s+(?P<key>[A-Za-z0-9_-]+)\s*-->\n"
    r"(?P<body>.*?)"
    r"\n<!--\s*/icdev:policy\s*-->",
    re.DOTALL,
)

#: Statuses a dispatcher can still hand to a session. Rewriting one of these is
#: the whole point; everything else is reported only.
DISPATCHABLE_STATUSES = ("backlog", "scheduled")

#: Terminal / in-flight statuses. Reported, never auto-rewritten — see SAFETY.
REPORT_ONLY_STATUSES = ("done", "in_progress", "pr_opened", "validating",
                        "failed", "suggested", "blocked")

_COMMENT_AUTHOR = "policy_drift"


class PolicyRuleError(ValueError):
    """A rules file that would let a rule touch rows it does not name."""


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Project:
    """A card: its id prefix and the policy text that is its single source."""

    key: str
    task_prefix: str
    policy: Optional[str]


@dataclass(frozen=True)
class Rule:
    """One legacy free-text form that should become a card-linked block."""

    id: str
    project: str
    task_id_prefixes: Tuple[str, ...]
    pattern: re.Pattern
    why: str
    exempt_task_ids: Tuple[str, ...] = ()

    def matches_task(self, task_id: str) -> bool:
        if task_id in self.exempt_task_ids:
            return False
        return any(task_id.startswith(p) for p in self.task_id_prefixes)


def load_projects(path: Optional[Path] = None) -> Dict[str, Project]:
    """Read ``args/projects.yaml`` into ``{key: Project}``.

    Entries without a ``key`` or ``task_prefix`` are skipped, matching what the
    Projects-in-Flight renderer in ``tools/dashboard/app.py`` already does — a
    malformed card should not take the checker down with it.

    Returns ``{}`` (loudly) rather than raising when the file is absent or
    unparseable. No cards means no policy blocks, which makes
    :func:`apply_policy_block` a no-op and :func:`scan` find nothing — the right
    degradation for a wheel install that ships no ``args/projects.yaml``, and
    the CLI checks for it explicitly so a human never reads "0 drifted" off a
    missing config.
    """
    import yaml

    src = Path(path) if path else PROJECTS_YAML
    try:
        raw = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.error("policy_drift: cannot read %s (%s) — no cards loaded", src, exc)
        return {}
    entries = raw.get("projects") or []
    out: Dict[str, Project] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").strip()
        prefix = str(entry.get("task_prefix") or "").strip()
        if not key or not prefix:
            continue
        policy = entry.get("policy")
        out[key] = Project(
            key=key,
            task_prefix=prefix,
            policy=str(policy).strip() if policy else None,
        )
    return out


@dataclass
class RuleSet:
    """Validated contents of ``args/kanban_policy_drift.yaml``."""

    rules: List[Rule] = field(default_factory=list)
    exempt_projects: Dict[str, str] = field(default_factory=dict)

    def is_exempt(self, project_key: str) -> bool:
        # "*" is the fail-closed sentinel :func:`load_exemptions` sets when it
        # cannot read the exemption list at all.
        return project_key in self.exempt_projects or "*" in self.exempt_projects


def load_exemptions(path: Optional[Path] = None) -> RuleSet:
    """Read ONLY ``exempt_projects`` — no rule validation, so it cannot raise.

    :func:`apply_policy_block` needs the exemption veto and nothing else, and it
    runs inside ``task_factory.create_tasks``. Routing the seeder through the
    full validating loader would let one malformed rule take down every seeder
    on the board, which is a worse failure than the drift this module exists to
    stop. The rules themselves are validated where they are used — by the
    checker, and by ``tests/test_kanban_policy_drift.py`` in CI.
    """
    import yaml

    src = Path(path) if path else RULES_YAML
    try:
        raw = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
        exempt = {str(k): str(v) for k, v in (raw.get("exempt_projects") or {}).items()}
    except (OSError, yaml.YAMLError) as exc:
        # Fail CLOSED on the thing that matters: an unreadable exemption list
        # means we cannot prove a card is safe to rewrite, so no card is.
        logger.error(
            "policy_drift: %s unreadable (%s) — treating EVERY project as exempt",
            src.name, exc,
        )
        return RuleSet(rules=[], exempt_projects={"*": f"{src.name} unreadable: {exc}"})
    return RuleSet(rules=[], exempt_projects=exempt)


def load_rules(
    path: Optional[Path] = None, projects: Optional[Dict[str, Project]] = None
) -> RuleSet:
    """Read and VALIDATE the rules file.

    Raises ``PolicyRuleError`` rather than silently narrowing, because every
    failure mode here is "a rule quietly edits rows nobody meant it to":

    * no ``applies_to.task_id_prefix``      → the rule is unscoped
    * a prefix that is not the project's    → the rule reaches another card
    * the project is in ``exempt_projects`` → a deliberate exception overridden
    * an uncompilable pattern               → silently matches nothing
    """
    import yaml

    src = Path(path) if path else RULES_YAML
    projects = projects if projects is not None else load_projects()

    raw = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    exempt = {
        str(k): str(v)
        for k, v in (raw.get("exempt_projects") or {}).items()
    }

    rules: List[Rule] = []
    seen_ids: set = set()
    for i, item in enumerate(raw.get("rules") or []):
        if not isinstance(item, dict):
            raise PolicyRuleError(f"rule #{i} is not a mapping")
        rid = str(item.get("id") or "").strip()
        if not rid:
            raise PolicyRuleError(f"rule #{i} has no id")
        if rid in seen_ids:
            raise PolicyRuleError(f"duplicate rule id {rid!r}")
        seen_ids.add(rid)

        project_key = str(item.get("project") or "").strip()
        if not project_key:
            raise PolicyRuleError(f"rule {rid!r} has no project")
        if project_key not in projects:
            raise PolicyRuleError(
                f"rule {rid!r} names project {project_key!r}, which is not in "
                f"{PROJECTS_YAML.name}"
            )
        if project_key in exempt:
            raise PolicyRuleError(
                f"rule {rid!r} targets {project_key!r}, which is listed in "
                f"exempt_projects ({exempt[project_key]}). An exemption is a "
                "deliberate decision; a rule may not quietly overrule it."
            )
        if not projects[project_key].policy:
            raise PolicyRuleError(
                f"rule {rid!r} targets project {project_key!r}, which has no "
                f"`policy:` field in {PROJECTS_YAML.name}. The replacement text "
                "comes from the card, so there is nothing to rewrite to."
            )

        applies = item.get("applies_to") or {}
        prefixes = tuple(
            str(p).strip() for p in (applies.get("task_id_prefix") or []) if str(p).strip()
        )
        if not prefixes:
            raise PolicyRuleError(
                f"rule {rid!r} declares no applies_to.task_id_prefix. A rule "
                "must name the rows it governs — an unscoped rule would edit "
                "every card on the board."
            )
        card_prefix = projects[project_key].task_prefix
        stray = [p for p in prefixes if not p.startswith(card_prefix)]
        if stray:
            raise PolicyRuleError(
                f"rule {rid!r} (project {project_key!r}, task_prefix "
                f"{card_prefix!r}) declares prefixes outside its own card: "
                f"{stray}. A rule may only touch its own project's rows."
            )

        raw_pattern = item.get("legacy_pattern")
        if not raw_pattern:
            raise PolicyRuleError(f"rule {rid!r} has no legacy_pattern")
        try:
            pattern = re.compile(str(raw_pattern), re.DOTALL)
        except re.error as exc:
            raise PolicyRuleError(f"rule {rid!r} pattern does not compile: {exc}") from exc

        why = str(item.get("why") or "").strip()
        if not why:
            raise PolicyRuleError(
                f"rule {rid!r} has no `why`. A rules table nobody can read is "
                "how the next person deletes the rule that was still needed."
            )

        exempt_ids = tuple(
            str(t).strip() for t in ((item.get("exempt") or {}).get("task_ids") or [])
        )
        rules.append(
            Rule(
                id=rid,
                project=project_key,
                task_id_prefixes=prefixes,
                pattern=pattern,
                why=why,
                exempt_task_ids=exempt_ids,
            )
        )

    return RuleSet(rules=rules, exempt_projects=exempt)


# ---------------------------------------------------------------------------
# Pure text transforms — no DB, no config. These are what the CI test exercises.
# ---------------------------------------------------------------------------

def render_block(project_key: str, policy_text: str) -> str:
    """Render the delimited, card-linked policy block for a description."""
    body = (policy_text or "").strip("\n")
    return f"{BLOCK_OPEN.format(key=project_key)}\n{body}\n{BLOCK_CLOSE}"


def find_block(description: str, project_key: str) -> Optional[re.Match]:
    """Return the match for *project_key*'s policy block, if the row has one."""
    for m in _BLOCK_RE.finditer(description or ""):
        if m.group("key") == project_key:
            return m
    return None


def sync_description(
    description: str,
    project_key: str,
    policy_text: str,
    rules: Sequence[Rule] = (),
    task_id: str = "",
) -> Tuple[str, Optional[str], Optional[str]]:
    """Bring one description in line with the card.

    Returns ``(new_description, action, detail)`` where *action* is:

    * ``None``              — already in sync, or nothing to key off
    * ``"block_updated"``   — the row had a block whose body had drifted
    * ``"legacy_migrated"`` — a rule's stale free text became a block

    A row is only ever migrated once: the replacement IS a block, so the next
    scan takes the ``block_updated`` path and the rule is irrelevant to it.
    """
    description = description or ""
    block = find_block(description, project_key)
    if block is not None:
        current = block.group("body").strip("\n")
        wanted = (policy_text or "").strip("\n")
        if current == wanted:
            return description, None, None
        new = (
            description[: block.start()]
            + render_block(project_key, policy_text)
            + description[block.end():]
        )
        return new, "block_updated", f"block body differed ({len(current)}→{len(wanted)} chars)"

    for rule in rules:
        if task_id and not rule.matches_task(task_id):
            continue
        m = rule.pattern.search(description)
        if not m:
            continue
        replaced = m.group(0)
        new = (
            description[: m.start()]
            + render_block(project_key, policy_text)
            + description[m.end():]
        )
        return new, "legacy_migrated", f"rule {rule.id}: replaced {len(replaced)} chars"

    return description, None, None


def project_for_task(task_id: str, projects: Dict[str, Project]) -> Optional[Project]:
    """Longest-matching ``task_prefix`` wins.

    ``projects.yaml`` permits nested prefixes (``dt-`` and ``dt-iqe-`` can both
    exist), so a first-match scan would attribute a row to the wrong card.
    """
    best: Optional[Project] = None
    for proj in projects.values():
        if task_id.startswith(proj.task_prefix):
            if best is None or len(proj.task_prefix) > len(best.task_prefix):
                best = proj
    return best


def apply_policy_block(description: str, task_id: str,
                       projects: Optional[Dict[str, Project]] = None,
                       ruleset: Optional[RuleSet] = None) -> str:
    """Seeder-facing helper: stamp the card's policy block onto a NEW description.

    A no-op unless the task's card carries a ``policy:`` and is not exempt, so
    it is safe to call unconditionally. Called from
    ``task_factory.create_tasks`` so freshly seeded rows are born card-linked
    and never need the legacy-pattern path at all.
    """
    projects = projects if projects is not None else load_projects()
    proj = project_for_task(task_id, projects)
    if proj is None or not proj.policy:
        return description
    if ruleset is not None and ruleset.is_exempt(proj.key):
        return description
    if find_block(description, proj.key) is not None:
        return description
    block = render_block(proj.key, proj.policy)
    body = (description or "").strip("\n")
    return f"{block}\n\n{body}" if body else block + "\n"


# ---------------------------------------------------------------------------
# Board scan / fix
# ---------------------------------------------------------------------------

def _rows(conn, statuses: Sequence[str]) -> List[dict]:
    placeholders = ",".join(["%s"] * len(statuses))
    sql = (
        "SELECT id, status, description FROM kanban_tasks "
        f"WHERE status IN ({placeholders}) ORDER BY id"
    )
    return [dict(r) for r in conn.execute(sql, tuple(statuses)).fetchall()]


def scan(
    conn,
    projects: Optional[Dict[str, Project]] = None,
    ruleset: Optional[RuleSet] = None,
    statuses: Sequence[str] = (),
    project_filter: Optional[str] = None,
) -> List[dict]:
    """Find every row whose embedded policy disagrees with its card.

    Read-only. Returns one finding per drifted row, carrying the rewritten text
    so :func:`apply` never has to recompute (and so a dry run shows exactly what
    a fix would write).
    """
    projects = projects if projects is not None else load_projects()
    ruleset = ruleset if ruleset is not None else load_rules(projects=projects)
    statuses = tuple(statuses) or (DISPATCHABLE_STATUSES + REPORT_ONLY_STATUSES)

    by_project: Dict[str, List[Rule]] = {}
    for rule in ruleset.rules:
        by_project.setdefault(rule.project, []).append(rule)

    findings: List[dict] = []
    for row in _rows(conn, statuses):
        task_id = row["id"]
        proj = project_for_task(task_id, projects)
        if proj is None or not proj.policy:
            continue
        if ruleset.is_exempt(proj.key):
            continue
        if project_filter and proj.key != project_filter:
            continue

        new_desc, action, detail = sync_description(
            row.get("description") or "",
            proj.key,
            proj.policy,
            by_project.get(proj.key, ()),
            task_id=task_id,
        )
        if not action:
            continue
        findings.append({
            "task_id": task_id,
            "status": row.get("status"),
            "project": proj.key,
            "action": action,
            "detail": detail,
            "old_description": row.get("description") or "",
            "new_description": new_desc,
            "fixable": row.get("status") in DISPATCHABLE_STATUSES,
        })
    return findings


def apply(conn, findings: Iterable[dict], fix_statuses: Sequence[str] = ()) -> dict:
    """Rewrite the drifted rows and append an audit comment for each.

    Only rows whose status is in *fix_statuses* (default
    :data:`DISPATCHABLE_STATUSES`) are written; the rest are counted as
    ``skipped`` with their status, so a report always says what it declined to
    touch rather than quietly covering fewer rows than it looked at.
    """
    fix_statuses = tuple(fix_statuses) or DISPATCHABLE_STATUSES
    now = datetime.now(timezone.utc).isoformat()
    written: List[str] = []
    skipped: List[dict] = []

    for f in findings:
        if f.get("status") not in fix_statuses:
            skipped.append({"task_id": f["task_id"], "status": f.get("status"),
                            "reason": "status not in fix set"})
            continue
        conn.execute(
            "UPDATE kanban_tasks SET description = %s, updated_at = %s WHERE id = %s",
            (f["new_description"], now, f["task_id"]),
        )
        conn.execute(
            "INSERT INTO kanban_task_comments (id, task_id, author, body, created_at) "
            "VALUES (%s,%s,%s,%s,%s)",
            (
                str(uuid.uuid4()),
                f["task_id"],
                _COMMENT_AUTHOR,
                (
                    f"policy_drift: {f['action']} for card '{f['project']}' "
                    f"({f.get('detail') or 'no detail'}). The description now "
                    f"carries a card-linked policy block; the text comes from "
                    f"`policy:` on the {f['project']} entry in args/projects.yaml."
                ),
                now,
            ),
        )
        written.append(f["task_id"])

    conn.commit()
    return {"written": written, "skipped": skipped}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fix", action="store_true",
                    help="rewrite drifted rows (default: report only)")
    ap.add_argument("--statuses", default="",
                    help="comma-separated statuses to SCAN "
                         "(default: every status)")
    ap.add_argument("--fix-statuses", default=",".join(DISPATCHABLE_STATUSES),
                    help="comma-separated statuses --fix may WRITE "
                         f"(default: {','.join(DISPATCHABLE_STATUSES)})")
    ap.add_argument("--project", default=None, help="limit to one card key")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    projects = load_projects()
    if not projects:
        print(f"error: no cards loaded from {PROJECTS_YAML} — nothing to check "
              "against. This is a missing config, not a clean board.",
              file=sys.stderr)
        return 2
    ruleset = load_rules(projects=projects)
    statuses = tuple(s.strip() for s in args.statuses.split(",") if s.strip())
    fix_statuses = tuple(s.strip() for s in args.fix_statuses.split(",") if s.strip())

    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        findings = scan(conn, projects, ruleset, statuses, args.project)
        result: Dict[str, Any] = {
            "drifted": len(findings),
            "rules": len(ruleset.rules),
            "exempt_projects": sorted(ruleset.exempt_projects),
            "findings": [
                {k: v for k, v in f.items()
                 if k not in ("old_description", "new_description")}
                for f in findings
            ],
            "fixed": [],
            "skipped": [],
            "dry_run": not args.fix,
        }
        if args.fix and findings:
            outcome = apply(conn, findings, fix_statuses)
            result["fixed"] = outcome["written"]
            result["skipped"] = outcome["skipped"]
    finally:
        conn.close()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"policy drift: {result['drifted']} row(s) out of sync with their card")
        for f in result["findings"]:
            print(f"  {f['task_id']:<24} {f['status']:<12} {f['action']}  {f['detail']}")
        if args.fix:
            print(f"rewrote {len(result['fixed'])}, skipped {len(result['skipped'])}")
        elif result["drifted"]:
            print("dry run — pass --fix to rewrite")
    return 0


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board as the
    # daemon. override=True: a pip-installed ICDEV in site-packages may have
    # already loaded a different checkout's .env at import.
    try:
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_REPO_ROOT / ".env", override=True)
    except ImportError:
        pass
    raise SystemExit(main())
