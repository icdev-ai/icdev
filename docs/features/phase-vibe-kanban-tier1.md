# CUI // SP-CTI
# Phase — Vibe-Kanban Tier 1 Adaptations

**Shipped:** 2026-05-07  
**Source:** Cherry-picked from BloopAI/vibe-kanban (sunsetting, 26k stars)

## What It Does

Four high-value UX and visibility enhancements to the ICDEV™ Kanban board, adapted from vibe-kanban's workspace model without a stack rewrite.

## Features

### 1. Change Metrics on Done Cards
Completed tasks now display a `N files +added -removed` badge showing exactly how much code changed. Captured from `git diff --stat` at merge time.

### 2. Phantom Completion Risk Badge
Tasks with a high output/claim ratio (`phantom_ratio > 0.5`) show an orange `⚠️ N% phantom` badge. Surfaces the existing `kanban_verifications.phantom_ratio` field that was tracked but never displayed.

### 3. Task Comments
Each task now has a collaborative comments thread. Open any task in the edit modal to read existing notes or post new ones. Useful for mid-task observations, context handoffs, and review notes.

### 4. Start Date / Target Date
Tasks can have a planning start date and a delivery target date. Target dates past due render red with an `OVERDUE` label on the card. Visible in all status columns.

## Key Files

| File | Change |
|------|--------|
| `tools/db/migrations/113_kanban_vibe_tier1/up.py` | Adds `start_date`, `target_date`, `files_changed`, `lines_added`, `lines_removed` to `kanban_tasks`; creates `kanban_task_comments` table |
| `tools/dashboard/api/kanban.py` | `list_tasks` LEFT JOINs latest `kanban_verifications` for `phantom_ratio`; `create/update_task` accepts `start_date`/`target_date`; `GET/POST /api/kanban/tasks/<id>/comments` |
| `tools/genesis/reflexes/kanban.py` | `_capture_diff_stats()` runs `git diff --stat` pre-merge; `_cleanup_worktree()` persists stats to DB |
| `tools/dashboard/templates/kanban.html` | Start/target date fields in modal; phantom badge, change metrics, overdue badge on cards; comments thread in edit modal |

## Tier 2 (Next)

- Dependency DAG visualization (Mermaid.js modal)
- PR/branch link on completion
- Custom tag system
