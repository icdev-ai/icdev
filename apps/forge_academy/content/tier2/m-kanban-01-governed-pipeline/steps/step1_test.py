
# Auto-grader — governed kanban pipeline

# ── can_transition ────────────────────────────────────────────────────────────
assert can_transition("backlog", "scheduled") is True
assert can_transition("scheduled", "in_progress") is True
assert can_transition("in_progress", "validating") is True
assert can_transition("validating", "done") is True
assert can_transition("suggested", "backlog") is True
# illegal jumps
assert can_transition("backlog", "done") is False
assert can_transition("suggested", "scheduled") is False, "quarantine must revive via backlog"
assert can_transition("done", "in_progress") is False, "done is terminal"
assert can_transition("bogus", "backlog") is False

# ── is_gate_task ──────────────────────────────────────────────────────────────
assert is_gate_task("prem-gate-00") is True
assert is_gate_task("iqe-gate-00") is True
assert is_gate_task("iqe-01") is False
assert is_gate_task("gate-00-thing") is False

# ── project_is_gated ──────────────────────────────────────────────────────────
tasks = [
    {"id": "proj-a-01", "status": "backlog", "project": "proj-a"},
    {"id": "proj-a-02", "status": "suggested", "project": "proj-a"},
    {"id": "proj-a-gate-00", "status": "done", "project": "proj-a"},
    {"id": "proj-b-01", "status": "backlog", "project": "proj-b"},
    {"id": "proj-b-gate-00", "status": "in_progress", "project": "proj-b"},
    {"id": "proj-c-01", "status": "scheduled", "project": "proj-c"},
]
assert project_is_gated(tasks, "proj-a") is False, "gate done → not gated"
assert project_is_gated(tasks, "proj-b") is True, "gate in_progress → gated"
assert project_is_gated(tasks, "proj-c") is False, "no gate task → not gated"

# ── promote_backlog_to_scheduled ──────────────────────────────────────────────
promoted = promote_backlog_to_scheduled(tasks)
assert promoted == ["proj-a-01"], f"only ungated non-suggested backlog task promotes: {promoted}"
# 'suggested' quarantine never promoted, gate task never promoted, gated project blocked
assert "proj-a-02" not in promoted
assert "proj-a-gate-00" not in promoted
assert "proj-b-01" not in promoted

# ── verify_done (origin/main gate) ────────────────────────────────────────────
merged = verify_done("proj-a-01", merged_to_origin_main=True)
assert merged == {"id": "proj-a-01", "status": "done", "verified": True}

unmerged = verify_done("proj-a-01", merged_to_origin_main=False)
assert unmerged["status"] == "validating", "no merge → stays in validating"
assert unmerged["verified"] is False

print("PASS: kanban lifecycle, gate-00 sentinel, quarantine, promoter, and done-gate all verified.")
