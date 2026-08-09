# CUI // SP-CTI
"""kax-merge-01 — land a kanban task's PR from the CLI.

``tools/kanban/cli.py --set-status <id> done`` only ever *gated* on merge: it
refuses while a branch carrying the task id has commits that are not on
``origin/<default>`` and offers ``--force-done --reason`` as the audited bypass.
Nothing in the CLI could ever *merge*, so a worker session that genuinely
finished had two options — be refused, or bypass the verification. Neither one
lands the work.

This module is the third option: **satisfy** the gate. ``preflight()`` proves the
PR is landable, ``land()`` merges it via the same ``gh pr merge --squash --auto``
call the watcher uses and then CONFIRMS the PR actually reached ``MERGED``
before the caller is allowed to write ``done``.

It is deliberately HARDER to satisfy than the refusal it replaces, never easier:

* **Fail-closed, everywhere.** ``_refuses_done`` in the CLI fails *open* by
  design — unreachable git, an absent branch or an import error must not wedge
  completions, because refusing to *write a row* is cheap. Landing a PR is not,
  so every unknown here refuses: unreadable PR state, unreadable verification,
  an unconfirmable merge.
* **It never reads ``KANBAN_REQUIRE_MERGE_FOR_DONE``.** That switch turns the
  local git heuristic off. It cannot turn any check here off, so the cases where
  plain ``done`` is *allowed* to bypass (toggle off, git unreachable, no branch
  found) grant ``--merge`` nothing.
* **It is not a softer ``--force-done``.** ``--force-done`` needs a sentence of
  prose. ``--merge`` needs an open PR on the default branch, green CI, no
  requested changes, a non-conflicting merge state, the enforced done-gate
  (``KANBAN_PIPELINE_ENFORCE`` + ``kanban_verifications.review_passed``), and a
  post-merge ``state == MERGED`` read from GitHub.

Prior art is reused rather than re-derived — a second implementation of the
enforced gate would drift from the one the watcher enforces:

* ``pr_watcher._enforced_done_ok``  — the KANBAN_PIPELINE_ENFORCE contract
* ``pr_watcher.PRWatcher._auto_merge``  — the gh invocation
* ``pr_watcher.PRWatcher._open_pr_files`` / ``._sibling_conflicts``  — the
  sibling-file-conflict guard
* ``pr_watcher.list_pr_tasks`` / ``fetch_pr_state`` / ``repo_default_branch``
* ``tools.ci.error_classifier``  — CI / review predicates

Library only (no CLI of its own); ``tools/kanban/cli.py --set-status <id> done
--merge`` is the entry point.
"""
from __future__ import annotations

import time
from typing import Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.kanban.land")

# How many times to re-read the PR after asking gh to merge it. `--squash
# --auto` can return success while GitHub queues the merge behind branch
# protection, so a single read would report "not merged" for a PR that lands two
# seconds later. Bounded: an unconfirmed merge refuses, it never assumes.
CONFIRM_ATTEMPTS = 3
CONFIRM_DELAY_SECONDS = 5.0


def _ck(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _refusal(task_id: str, reason: str, pr_url: Optional[str] = None,
             checks: Optional[list] = None) -> dict:
    return {
        "task_id": task_id,
        "ok": False,
        "merged": False,
        "pr_url": pr_url,
        "reason": reason,
        "checks": list(checks or []),
    }


def _build_watcher(get_conn):
    """A PRWatcher wired for one-shot CLI use.

    ``dry_run`` is hardcoded False: ``_auto_merge`` returns True *without
    merging* under dry_run, and a caller that trusted that would mark a task
    done with the work still unmerged — precisely the bug this whole gate
    exists to prevent. ``land(dry_run=True)`` stops before the merge instead.

    ``auto_merge_enabled`` is forced on because that key governs *unattended*
    merges by the watcher daemon; ``--merge`` is a human asking for this one PR.
    Every verification gate below still applies and none of them is
    configurable off.
    """
    from tools.ci.pr_watcher import PRWatcher, load_config

    cfg = dict(load_config() or {})
    cfg["auto_merge_enabled"] = True
    return PRWatcher(config=cfg, get_connection=get_conn, dry_run=False)


def _resolve_conn(get_conn):
    if get_conn is not None:
        return get_conn
    from tools.db.storage import get_connection

    return get_connection


def preflight(task_id: str, *, get_conn=None, watcher=None) -> dict:
    """Prove ``task_id``'s PR may be landed. Never merges anything.

    Returns ``{"ok": bool, "reason": str, "pr_url": str|None, "checks": [...],
    "already_merged": bool}``. ``ok`` is True only when every check passed;
    ``reason`` names the first one that did not.
    """
    get_conn = _resolve_conn(get_conn)
    from tools.ci import error_classifier as ec
    from tools.ci import pr_watcher as prw

    w = watcher if watcher is not None else _build_watcher(get_conn)
    checks: list = []
    pr_url: Optional[str] = None

    # ── (a) a PR must exist ────────────────────────────────────────────────
    try:
        tasks = prw.list_pr_tasks(get_conn, task_id)
    except Exception as exc:  # noqa: BLE001 — fail closed, never merge blind
        return _refusal(task_id, f"task lookup failed: {exc}", checks=checks)
    if not tasks:
        return _refusal(
            task_id,
            "no PR is recorded for this task (executor_url / description carry "
            "no github PR url) — open a PR first",
            checks=checks,
        )
    pr_url = tasks[0].get("pr_url")
    checks.append(_ck("pr_recorded", True, pr_url or ""))

    try:
        state = w._fetch_state(pr_url)
    except Exception as exc:  # noqa: BLE001
        checks.append(_ck("pr_readable", False, str(exc)[:200]))
        return _refusal(
            task_id,
            f"PR state unreadable — refusing to merge blind: {exc}",
            pr_url, checks,
        )
    checks.append(_ck("pr_readable", True))

    # ── (a) …and be OPEN ───────────────────────────────────────────────────
    top_state = (state.get("state") or "").upper()
    already_merged = top_state == "MERGED"
    if not already_merged and top_state != "OPEN":
        checks.append(_ck("pr_open", False, top_state or "<unknown>"))
        return _refusal(
            task_id,
            f"PR state is '{top_state or '<unknown>'}', not OPEN — nothing to "
            f"land ({pr_url})",
            pr_url, checks,
        )
    checks.append(_ck("pr_open", True, top_state))

    if already_merged:
        # The strongest possible evidence the work is on the default branch, so
        # there is nothing left to merge. Still subject to the enforced
        # done-gate below — a merged PR does not excuse a failed conformance
        # review, it just means the merge step is a no-op.
        logger.info("land: %s PR already MERGED (%s)", task_id, pr_url)
    else:
        # Base-branch guard (incident 2026-07-08, PR #114): merging into a
        # feature branch strands the change off-main. Unknown base = unsafe.
        base_ref = (state.get("baseRefName") or "").strip()
        default_branch = w._default_branch()
        if base_ref != default_branch:
            checks.append(_ck("base_is_default", False,
                              f"{base_ref or '<unknown>'} != {default_branch}"))
            return _refusal(
                task_id,
                f"PR base '{base_ref or '<unknown>'}' is not the default branch "
                f"'{default_branch}' — merging it would strand the work off-main",
                pr_url, checks,
            )
        checks.append(_ck("base_is_default", True, base_ref))

        if ec.is_merge_conflict(state):
            checks.append(_ck("mergeable", False, "CONFLICTING"))
            return _refusal(
                task_id,
                "PR is CONFLICTING — rebase it onto the default branch first",
                pr_url, checks,
            )
        checks.append(_ck("mergeable", True, (state.get("mergeable") or "")))

        if ec.is_changes_requested(state):
            checks.append(_ck("no_changes_requested", False, "CHANGES_REQUESTED"))
            return _refusal(
                task_id,
                "a reviewer requested changes — address the review first",
                pr_url, checks,
            )
        checks.append(_ck("no_changes_requested", True))

        # ── (b) CI green ───────────────────────────────────────────────────
        if ec.is_ci_failed(state):
            checks.append(_ck("ci_green", False, "a check failed"))
            return _refusal(task_id, "CI is red — fix the failing checks first",
                            pr_url, checks)
        if ec.is_in_progress(state):
            checks.append(_ck("ci_green", False, "checks still running"))
            return _refusal(task_id, "CI is still running — wait for it to finish",
                            pr_url, checks)
        if not ec.is_passing(state):
            # An empty rollup lands here: no CI has reported, which is unknown,
            # not green.
            checks.append(_ck("ci_green", False, "no conclusive green rollup"))
            return _refusal(
                task_id,
                "CI is not green (no conclusive successful check rollup)",
                pr_url, checks,
            )
        checks.append(_ck("ci_green", True))

        if w.config.get("auto_merge_require_approval", True):
            if not ec.is_approved(state):
                checks.append(_ck("approved", False, "no APPROVED review"))
                return _refusal(
                    task_id,
                    "an approving review is required (auto_merge_require_approval)",
                    pr_url, checks,
                )
            checks.append(_ck("approved", True))

    # ── (c) the enforced done-gate — reused, never re-derived ──────────────
    gate_ok, gate_reason = prw._enforced_done_ok(get_conn, task_id)
    checks.append(_ck("enforced_done_gate", gate_ok, gate_reason))
    if not gate_ok:
        return _refusal(task_id, gate_reason, pr_url, checks)

    # ── (d) sibling-file conflict, when the operator opted into holding ────
    if not already_merged and w.config.get("hold_on_sibling_conflict", False):
        file_map = w._open_pr_files()
        if pr_url not in file_map:
            # `_open_pr_files` returns {} on any gh failure, and this PR is open,
            # so its own absence means the listing failed rather than that there
            # are no siblings. The operator asked for merges to be serialized;
            # we cannot verify that here, so refuse instead of racing.
            checks.append(_ck("no_sibling_conflict", False, "open-PR listing unavailable"))
            return _refusal(
                task_id,
                "hold_on_sibling_conflict is set but the open-PR file listing "
                "could not be read — refusing to merge without the check",
                pr_url, checks,
            )
        sib = w._sibling_conflicts(pr_url, file_map)
        if sib:
            detail = "; ".join(
                f"{u} [{', '.join(sorted(fs))}]" for u, fs in sib.items())
            checks.append(_ck("no_sibling_conflict", False, detail))
            return _refusal(
                task_id,
                f"held: shares source file(s) with {len(sib)} open PR(s) — "
                f"{detail}",
                pr_url, checks,
            )
        checks.append(_ck("no_sibling_conflict", True))

    return {
        "task_id": task_id,
        "ok": True,
        "merged": False,
        "already_merged": already_merged,
        "pr_url": pr_url,
        "reason": ("PR already merged; every gate passed" if already_merged
                   else "every gate passed — PR is landable"),
        "checks": checks,
    }


def land(
    task_id: str,
    *,
    get_conn=None,
    watcher=None,
    dry_run: bool = False,
    sleeper=None,
    confirm_attempts: int = CONFIRM_ATTEMPTS,
) -> dict:
    """Preflight, merge, then CONFIRM the merge landed.

    ``ok`` is True only when the PR is observed ``MERGED`` (or already was).
    Asking gh to merge is not evidence that it merged: ``--auto`` can queue the
    merge behind branch protection and still exit 0, so the caller must not
    write ``done`` on the strength of the request alone.

    ``dry_run`` runs the preflight and stops — it never reports a merge.
    """
    get_conn = _resolve_conn(get_conn)
    w = watcher if watcher is not None else _build_watcher(get_conn)

    verdict = preflight(task_id, get_conn=get_conn, watcher=w)
    if not verdict.get("ok"):
        return verdict

    pr_url = verdict["pr_url"]
    if verdict.get("already_merged"):
        return {**verdict, "merged": True}

    if dry_run:
        return {
            **verdict,
            "merged": False,
            "dry_run": True,
            "reason": "preflight passed — nothing merged (--dry-run)",
        }

    if not w._auto_merge(pr_url):
        verdict["checks"].append(_ck("merge_requested", False, "gh pr merge failed"))
        return _refusal(
            task_id,
            f"gh pr merge failed for {pr_url} — the task was NOT marked done",
            pr_url, verdict["checks"],
        )
    verdict["checks"].append(_ck("merge_requested", True))

    sleeper = sleeper or time.sleep
    attempts = max(1, int(confirm_attempts))
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            state = w._fetch_state(pr_url)
            last = (state.get("state") or "").upper()
        except Exception as exc:  # noqa: BLE001 — an unreadable PR is not a merge
            last = f"unreadable ({exc})"
        if last == "MERGED":
            verdict["checks"].append(_ck("merge_confirmed", True, "MERGED"))
            return {
                **verdict,
                "merged": True,
                "reason": f"merged and confirmed: {pr_url}",
            }
        if attempt < attempts:
            sleeper(CONFIRM_DELAY_SECONDS)

    verdict["checks"].append(_ck("merge_confirmed", False, last or "<unknown>"))
    return _refusal(
        task_id,
        f"merge was requested but the PR is still '{last or '<unknown>'}' — "
        f"gh may be queueing it behind branch protection. The task was NOT "
        f"marked done; re-run once it lands.",
        pr_url, verdict["checks"],
    )
