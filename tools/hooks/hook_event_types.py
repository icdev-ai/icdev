# CUI // SP-CTI
"""The ONE statement of which ``hook_type`` values ``hook_events`` admits (xrv-mem-01).

``hook_events.hook_type`` carries a CHECK constraint. Until this module existed
it was spelled as a literal in ``tools/db/init_icdev_db.py`` and NOWHERE else
was consulted, so the hooks and the constraint drifted apart in the one
direction nothing watches: a hook can write a name the CHECK refuses, its
``except Exception: pass`` swallows the refusal, and the table still looks
healthy.

MEASURED 2026-09-11 on both live backends (PostgreSQL ``icdev`` and the
SQLite ``data/icdev.db`` the hooks actually write to through
``.claude/hooks/send_event.py``):

    admitted by the CHECK   pre_tool_use post_tool_use notification stop
                            subagent_stop  (+ twin_drift on PG only)
    written by a hook       ... plus user_prompt_submit (since the
                            UserPromptSubmit hook was authored) and
                            pre_compact (since the PreCompact hook was)
    rows for those two      0 on PG, 0 on SQLite, against 42,569 / 341,173
                            post_tool_use rows

Every user-prompt and pre-compact event this platform has ever recorded was
refused by the constraint. Same shape as ``oscal_generated`` (migration
20260902235404): a writer whose name was never admitted.

The constant below is what ``init_icdev_db.py`` templates its CHECK from and
what migration 20260911220746 rebuilds the live PostgreSQL constraint from.
Adding a hook means adding its name HERE and scaffolding a migration that
calls :func:`rebuild_hook_type_constraint`; nothing else needs a second copy.

stdlib-only on purpose: ``init_icdev_db.py`` and a migration import it, and a
constraint helper that dragged the storage layer in would make ``init`` depend
on the thing it initialises.
"""
from __future__ import annotations

#: Every hook_type a writer may INSERT. Order is presentation only.
#:
#: ``twin_drift`` is carried because the live PostgreSQL constraint admits it
#: (measured 2026-09-11); a rebuild that dropped a name the deployed CHECK
#: admits would be a narrowing this module has no evidence for.
HOOK_EVENT_TYPES: tuple[str, ...] = (
    "pre_tool_use",
    "post_tool_use",
    "notification",
    "stop",
    "subagent_stop",
    "twin_drift",
    "user_prompt_submit",
    "pre_compact",
    "session_start",
)

#: The constraint's name on PostgreSQL — the one the live table carries
#: (``pg_get_constraintdef`` on 2026-09-11), so DROP IF EXISTS finds it.
HOOK_TYPE_CONSTRAINT = "hook_events_hook_type_check"


def hook_type_values_sql() -> str:
    """The quoted, comma-joined value list — the CHECK body's inside.

    Hook types are identifiers from the hardcoded tuple above, never user
    input, so inlining them as literals is safe (and DDL cannot be
    parameterised anyway).
    """
    return ", ".join(f"'{t}'" for t in HOOK_EVENT_TYPES)


def hook_type_check_sql() -> str:
    """The CHECK clause admitting exactly HOOK_EVENT_TYPES."""
    return f"CHECK (hook_type IN ({hook_type_values_sql()}))"


def rebuild_hook_type_constraint(conn) -> bool:
    """Regenerate hook_events' hook_type CHECK from HOOK_EVENT_TYPES.

    Call this from a migration whenever a hook type is added. Returns True when
    the constraint was rebuilt, False on SQLite — SQLite cannot ALTER a CHECK,
    and rebuilding ``hook_events`` there would mean copying an append-only
    table (it is in ``APPEND_ONLY_TABLES``). Fresh SQLite databases get the
    generated constraint from ``init_icdev_db.py`` instead; an existing one
    keeps refusing the new names until it is re-initialised, and that is
    STATED by the caller rather than hidden — see the migration's docstring.

    The connection is caller-owned: migrations run inside a larger
    transaction and closing it here would break the rest of their run.
    """
    if getattr(conn, "_backend", "") != "postgresql":
        return False
    conn.execute(
        f"ALTER TABLE hook_events DROP CONSTRAINT IF EXISTS {HOOK_TYPE_CONSTRAINT}"
    )
    conn.execute(
        f"ALTER TABLE hook_events ADD CONSTRAINT {HOOK_TYPE_CONSTRAINT} "
        f"{hook_type_check_sql()}"
    )
    conn.commit()
    return True
