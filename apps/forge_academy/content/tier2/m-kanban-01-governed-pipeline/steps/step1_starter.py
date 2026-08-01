
"""
Tier 2 — The Governed Delivery Pipeline
Goal: Model the kanban task lifecycle and the gates that hold work back — the
      manual-mode gate-00 sentinel, 'suggested' quarantine, and done-verification
      against origin/main.

ICDEV ships every multi-task build through a governed kanban pipeline (tools/kanban/).
Tasks move backlog -> scheduled -> in_progress -> validating -> done, and the promoter
(promote_backlog_to_scheduled) dispatches them. Two gates matter here:
  * A manual-only project holds a `<prefix>-gate-00` sentinel task in_progress so the
    promoter NEVER auto-dispatches that project's tasks.
  * AI-authored 'suggested' tasks sit in quarantine until revived (never promoted directly).
A task is only truly `done` once its PR is merged to origin/main (done-verification).
This exercise builds those rules with the stdlib.
"""

# Allowed status transitions. A status maps to the set of statuses it may move to.
LIFECYCLE = {
    "suggested":   {"backlog"},              # revived out of quarantine
    "backlog":     {"scheduled"},
    "scheduled":   {"in_progress"},
    "in_progress": {"validating", "backlog"},
    "validating":  {"done", "in_progress"},
    "done":        set(),                    # terminal
}


# ── Step 1: Transition rule ───────────────────────────────────────────────────

def can_transition(current: str, target: str) -> bool:
    """TODO: Return True iff `target` is a legal next status for `current`.

    Use LIFECYCLE. Unknown `current` returns False.
    """
    # YOUR CODE HERE
    pass


# ── Step 2: The manual gate-00 sentinel ───────────────────────────────────────

def is_gate_task(task_id: str) -> bool:
    """TODO: Return True if this is a manual gate sentinel — its id ends with
    '-gate-00' (e.g. 'prem-gate-00'). These are never dispatched themselves."""
    # YOUR CODE HERE
    pass


def project_is_gated(tasks: list, project: str) -> bool:
    """TODO: Return True if `project` has a gate-00 task that is NOT yet done.

    A gate task held in any non-'done' status (e.g. in_progress) blocks the whole
    project. Look through `tasks` (each a dict with 'id', 'status', 'project') for a
    gate task (is_gate_task) belonging to `project` whose status != 'done'.
    """
    # YOUR CODE HERE
    pass


# ── Step 3: The promoter ──────────────────────────────────────────────────────

def promote_backlog_to_scheduled(tasks: list) -> list:
    """TODO: Return the ids of tasks the promoter would move backlog -> scheduled.

    A task is promoted only if ALL hold:
      * its status == "backlog"            (not 'suggested' quarantine, not already moving)
      * it is NOT a gate task              (is_gate_task is False)
      * its project is NOT gated           (project_is_gated is False)
    Preserve the input order.
    """
    # YOUR CODE HERE
    pass


# ── Step 4: Done-verification against origin/main ─────────────────────────────

def verify_done(task_id: str, merged_to_origin_main: bool) -> dict:
    """TODO: A task is only 'done' once its PR is merged to origin/main.

    Return {"id": task_id, "status": "done", "verified": True} when merged,
    otherwise {"id": task_id, "status": "validating", "verified": False}
    (it stays in validating until the merge lands).
    """
    # YOUR CODE HERE
    pass


# Demo
if __name__ == "__main__":
    tasks = [
        {"id": "proj-a-01", "status": "backlog", "project": "proj-a"},
        {"id": "proj-a-gate-00", "status": "done", "project": "proj-a"},
        {"id": "proj-b-01", "status": "backlog", "project": "proj-b"},
        {"id": "proj-b-gate-00", "status": "in_progress", "project": "proj-b"},
    ]
    print("promote:", promote_backlog_to_scheduled(tasks))
    print("done check:", verify_done("proj-a-01", merged_to_origin_main=False))
