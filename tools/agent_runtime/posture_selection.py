#!/usr/bin/env python3
# CUI // SP-CTI
"""Selecting a permission posture, recorded as operator INTENT — hcx-post-02.

hcx-post-01 named the combination of safety knobs and gave it a resolution
order. It did not give anybody a way to *choose* one at runtime, and it recorded
nothing when a choice was made. This module is that half.

WHY A SEPARATE EVENT AT ALL
===========================
The resolved knobs already say what the posture IS. They can never say who
decided it, or when, or what it was before — a knob carries state, and a state
has no author. Reading ``approval_mode == "off"`` out of a running process tells
you an approval prompt is not going to appear; it does not tell you whether that
was a deployment default nobody looked at or something a named operator turned
off eleven minutes ago, and those two facts call for completely different
responses.

So the record is a separate act with its own row, in the same append-only log
and the same ``seq`` ordering as the turns it governs
(:data:`~tools.agent_runtime.event_log.EVENT_TYPES`), which makes "the posture
widened, and then these four tool calls happened" one ``ORDER BY seq`` rather
than a join between two clocks.

THE EVENT IS LOG-ONLY, AND IT IS WRITTEN FIRST
==============================================
:func:`select_posture` appends the event and only then changes anything. The
event changes nothing by itself: nothing reads it back to decide a knob, and
deleting every row would alter no behaviour. That ordering is the point —

* an intent recorded before the act survives a crash *during* the act, and
* a reader who finds an intent with no following change learns something real
  (the apply failed), which the reverse ordering would have hidden.

RE-SELECTING THE EFFECTIVE POSTURE APPENDS NOTHING
==================================================
A selection that would move neither the posture name nor any knob is not a
decision, and writing a row for it would make the log grow with the number of
times somebody typed ``/posture`` to *look*. :attr:`Selection.changed` is False
and :attr:`Selection.logged` is False on that path. Deliberately narrow: it is
"same name AND no knob delta", so a genuine re-selection that would move a knob
(because the file changed under the process, say) still records.

WHAT "SETS EACH KNOB" MEANS HERE, AND THE ONE THING THIS REFUSES TO DO
=====================================================================
Selecting a posture writes exactly one variable —
``ICDEV_PERMISSION_POSTURE`` — and the four knobs follow through hcx-post-01's
existing chain::

    explicit call argument > env var > agent_runtime.yaml > posture > built-in

It deliberately does NOT write the four per-knob environment variables, and the
consequence has to be stated because it cuts the dangerous way: a knob already
pinned by ``ICDEV_SAG_APPROVAL_MODE`` (or by an explicit ``agent_runtime.yaml``
key) does **not** move, even when the operator is TIGHTENING. An operator who
selects ``workspace-write`` while ``ICDEV_SAG_ALLOW_MUTATION=1`` is exported has
not disabled mutation.

The alternative — having the selection overwrite those variables — was rejected
because it reverses an intent stated at a layer hcx-post-01 explicitly put
*above* this one, and it would do so invisibly. Instead every such knob is
reported as PINNED: in the ``/posture`` output, in the returned
:attr:`Selection.pinned`, and in the event payload. Under-delivering loudly is
recoverable; over-delivering silently is not, and a posture layer that quietly
edited the environment out from under a systemd unit would be the second kind.

AN UNWRITABLE LOG REFUSES TO WIDEN, AND ONLY TO WIDEN
=====================================================
If the event cannot be appended, a posture flagged ``requires_explicit_selection``
— the flag hcx-post-01 uses for exactly "this widens the blast radius" — is
REFUSED. There is no unaudited widening.

Any other posture is applied anyway, with ``logged: False`` on the result and a
warning in the log. The asymmetry is the point: refusing across the board would
mean an unreachable database prevents an operator from TIGHTENING, leaving them
in the looser posture — the audit log failing closed onto the less safe of the
two states.

CLI::

    python -m tools.agent_runtime.posture_selection --json
    python -m tools.agent_runtime.posture_selection --list
    python -m tools.agent_runtime.posture_selection --select workspace-write \\
        --session <context-id> --actor <who>
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Sequence

from tools.agent_runtime.config import (
    ENV_POSTURE,
    POSTURE_EXPLICIT_KEY,
    AgentRuntimeConfig,
    load_config,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger("agent_runtime.posture_selection")

#: The event type appended on selection. A member of
#: :data:`~tools.agent_runtime.event_log.EVENT_TYPES`, so it needed no migration
#: and no CHECK constraint edit.
EVENT_TYPE = "permission_posture"


@dataclasses.dataclass(frozen=True)
class Knob:
    """One posture-governed safety knob, and the layers that can hold it.

    ``read`` resolves the knob against a given config object rather than against
    the process, which is what makes a before/after delta computable without
    mutating anything to find out.
    """

    key: str
    dotted: str
    env: str
    read: Callable[[AgentRuntimeConfig], Any]


#: The four knobs a posture names, in the order ``/posture`` prints them:
#: outermost confinement first, then the two gates, then the mutation switch.
#: This is the same set ``args/permission_postures.yaml`` declares — a knob here
#: with no key there would report ``(not declared)`` forever, and a key there
#: with no entry here would never appear in a delta.
KNOBS: tuple[Knob, ...] = (
    Knob(
        "sandbox",
        "subsystems.sandbox.mode",
        "ICDEV_SAG_SANDBOX_MODE",
        lambda cfg: cfg.sandbox_mode,
    ),
    Knob(
        "approval_mode",
        "subsystems.approval.mode",
        "ICDEV_SAG_APPROVAL_MODE",
        lambda cfg: cfg.approval_mode,
    ),
    Knob(
        "command_approval_mode",
        "subsystems.approval.command_mode",
        "ICDEV_AGENT_APPROVAL_MODE",
        lambda cfg: cfg.command_approval_mode,
    ),
    Knob(
        "allow_mutation",
        "subsystems.mutation.allow",
        "ICDEV_SAG_ALLOW_MUTATION",
        lambda cfg: cfg.allow_mutation,
    ),
)

#: Why a selection did not happen. Never merged into one "failed": an operator
#: who typed a name wrong and an operator whose audit log is down need different
#: next actions.
REFUSED_UNKNOWN = "unknown_posture"
REFUSED_UNAUDITED_WIDENING = "unaudited_widening"


# ---------------------------------------------------------------------------
# Reading the current state
# ---------------------------------------------------------------------------
def _same(a: Any, b: Any) -> bool:
    """Compare two knob values the way the resolver produced them.

    ``text(choices=...)`` lowercases what it returns and ``flag`` returns a real
    bool, so a case difference between a YAML literal and a resolved value is
    not a change and must not be reported as one. Booleans are compared as
    booleans, because ``False == 0`` is true and ``"false" == False`` is not.
    """
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b) and isinstance(a, bool) == isinstance(b, bool)
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
    return a == b


def _pinned_by(cfg: AgentRuntimeConfig, knob: Knob) -> str:
    """Which higher layer holds ``knob``, or ``""`` when the posture governs it.

    Attribution only — whether a knob is pinned at all is decided empirically by
    :func:`knob_deltas`, by observing that the resolved value did not match what
    the posture declared. Predicting it instead would mean re-implementing
    ``text(choices=...)``'s fall-through and ``_as_bool``'s coercion here, i.e.
    a second copy of the precedence rules that drifts the first time one changes.
    """
    raw = os.environ.get(knob.env)
    if raw is not None and str(raw).strip():
        return knob.env
    if cfg.get(knob.dotted) is not None:
        return f"agent_runtime.yaml:{knob.dotted}"
    # Neither layer holds it, so nothing overrode the posture — the posture's
    # own declared value was discarded by the resolver instead (a mode outside
    # `choices`, a non-boolean where a bool was wanted). Named as what it is,
    # because "pinned by nothing" would send a reader hunting an env var that
    # was never set.
    return "a rejected posture value (not accepted by the resolver)"


def effective_knobs(cfg: Optional[AgentRuntimeConfig] = None) -> dict[str, Any]:
    """The four knob values as they resolve right now."""
    active = cfg if cfg is not None else load_config()
    return {knob.key: knob.read(active) for knob in KNOBS}


@dataclasses.dataclass(frozen=True)
class KnobDelta:
    """What one knob would do under a proposed posture."""

    knob: str
    before: Any
    after: Any
    #: The posture's own declared value, or ``None`` when it declares none.
    declared: Any = None
    #: Set when the posture declared a value the resolution did not adopt — the
    #: knob is held above the posture layer. Names the holder.
    pinned_by: str = ""

    @property
    def changed(self) -> bool:
        return not _same(self.before, self.after)

    @property
    def pinned(self) -> bool:
        return bool(self.pinned_by)

    def to_dict(self) -> dict[str, Any]:
        return {
            "knob": self.knob,
            "before": self.before,
            "after": self.after,
            "declared": self.declared,
            "changed": self.changed,
            "pinned_by": self.pinned_by,
        }


def knob_deltas(
    posture: str, cfg: Optional[AgentRuntimeConfig] = None
) -> list[KnobDelta]:
    """What each knob would become if ``posture`` were selected.

    Computed by resolving a ``posture_override`` copy of the config through the
    ordinary chain, so this answers with the real resolver and not with a
    prediction of it. Nothing in the process is mutated.
    """
    before_cfg = cfg if cfg is not None else load_config()
    after_cfg = dataclasses.replace(before_cfg, posture_override=posture)
    declared = before_cfg.posture_set.postures.get(posture, {})

    deltas: list[KnobDelta] = []
    for knob in KNOBS:
        before = knob.read(before_cfg)
        after = knob.read(after_cfg)
        want = declared.get(knob.key)
        pinned = ""
        if want is not None and not _same(after, want):
            pinned = _pinned_by(before_cfg, knob)
        deltas.append(
            KnobDelta(
                knob=knob.key,
                before=before,
                after=after,
                declared=want,
                pinned_by=pinned,
            )
        )
    return deltas


def describe(cfg: Optional[AgentRuntimeConfig] = None) -> dict[str, Any]:
    """The posture in force, how it was chosen, and its knobs — for ``/posture``.

    ``pinned`` here is computed against the posture already in force, so it
    answers "which of these values did the posture NOT put there" — the question
    an operator has when a knob does not read the way the posture file says it
    should.
    """
    active = cfg if cfg is not None else load_config()
    name, source = active.posture_set.resolve_name(active.posture_override)
    deltas = knob_deltas(name, active)
    return {
        "posture": name,
        "source": source,
        "postures_path": (
            str(active.posture_set.path) if active.posture_set.path else None
        ),
        "knobs": {d.knob: d.after for d in deltas},
        "pinned": {d.knob: d.pinned_by for d in deltas if d.pinned},
        "available": available_postures(active),
    }


def available_postures(cfg: Optional[AgentRuntimeConfig] = None) -> dict[str, Any]:
    """Every selectable posture, with its description and explicit-only flag."""
    active = cfg if cfg is not None else load_config()
    out: dict[str, Any] = {}
    for name, values in sorted(active.posture_set.postures.items()):
        out[name] = {
            "description": str(values.get("description") or "").strip(),
            "requires_explicit_selection": bool(values.get(POSTURE_EXPLICIT_KEY)),
            "knobs": {k.key: values.get(k.key) for k in KNOBS},
        }
    return out


# ---------------------------------------------------------------------------
# The selection
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class Selection:
    """The outcome of one :func:`select_posture` call."""

    requested: str
    previous: str
    #: The posture in force when this returned. Equals ``requested`` on an
    #: applied selection and ``previous`` on every refusal.
    posture: str
    changed: bool
    applied: bool
    logged: bool
    deltas: tuple[KnobDelta, ...] = ()
    refused: str = ""
    reason: str = ""
    event_id: str = ""
    actor: str = ""
    session_id: str = ""

    @property
    def moved(self) -> tuple[KnobDelta, ...]:
        return tuple(d for d in self.deltas if d.changed)

    @property
    def pinned(self) -> tuple[KnobDelta, ...]:
        return tuple(d for d in self.deltas if d.pinned)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "previous": self.previous,
            "posture": self.posture,
            "changed": self.changed,
            "applied": self.applied,
            "logged": self.logged,
            "refused": self.refused,
            "reason": self.reason,
            "event_id": self.event_id,
            "actor": self.actor,
            "session_id": self.session_id,
            "deltas": [d.to_dict() for d in self.deltas],
        }

    def summary(self) -> str:
        """Operator-facing text. Says what did NOT move as clearly as what did."""
        if self.refused:
            return f"Posture unchanged ({self.posture}). {self.reason}"
        if not self.changed:
            return (
                f"Already on posture '{self.posture}' — nothing changed, and "
                "nothing was recorded."
            )
        lines = [f"Posture: {self.previous} -> {self.posture} (actor: {self.actor or 'unknown'})"]
        for d in self.moved:
            lines.append(f"  {d.knob}: {d.before!r} -> {d.after!r}")
        if not self.moved:
            lines.append("  (no knob moved — every one is pinned above the posture layer)")
        for d in self.pinned:
            lines.append(
                f"  NOT MOVED  {d.knob} stays {d.after!r}; the posture asks for "
                f"{d.declared!r} but {d.pinned_by} pins it. Unset it to let the "
                "posture govern."
            )
        if not self.logged:
            lines.append(
                "  WARNING: this change was NOT recorded in agent_session_events "
                f"({self.reason})"
            )
        elif self.event_id:
            lines.append(f"  recorded as {self.event_id}")
        return "\n".join(lines)


def select_posture(
    name: str,
    *,
    actor: str = "",
    session_id: str = "",
    correlation_id: str = "",
    tenant_id: Optional[str] = None,
    classification: Optional[str] = None,
    cfg: Optional[AgentRuntimeConfig] = None,
    appender: Optional[Callable[..., Any]] = None,
    environ: Optional[dict[str, str]] = None,
) -> Selection:
    """Select ``name`` as the permission posture, recording the operator's intent.

    Order of operations, and it is the design rather than an implementation
    detail: validate, compute the delta, append the event, *then* apply.

    Args:
        name: The posture to select. Must be one the loaded
            :class:`~tools.agent_runtime.config.PostureSet` knows;
            ``requires_explicit_selection`` is satisfied by this call being one.
        actor: Who chose it. Recorded verbatim — this is the column the whole
            event exists for, and an empty one is logged as ``unknown`` rather
            than omitted, so a caller that forgot to pass it is visible.
        session_id: The chat context id the event is filed under. Without one
            there is nowhere to append; the selection is then treated exactly as
            an unwritable log.
        cfg: Config to resolve against. Defaults to the process-wide one.
        appender: The write function, for tests. Defaults to ``event_log.append``.
        environ: The mapping the selection is applied to. Defaults to
            ``os.environ``.

    Returns:
        A :class:`Selection`. Never raises for an operator error — a bad name is
        a refusal with a reason, not a traceback in a REPL.
    """
    env = os.environ if environ is None else environ
    active = cfg if cfg is not None else load_config()
    posture_set = active.posture_set
    previous, _source = posture_set.resolve_name(active.posture_override)
    requested = (name or "").strip()

    known = posture_set.postures
    if requested not in known:
        return Selection(
            requested=requested,
            previous=previous,
            posture=previous,
            changed=False,
            applied=False,
            logged=False,
            refused=REFUSED_UNKNOWN,
            reason=(
                f"Unknown posture {requested!r}. Known: {', '.join(sorted(known))}."
            ),
            actor=actor,
            session_id=session_id,
        )

    deltas = tuple(knob_deltas(requested, active))
    changed = requested != previous or any(d.changed for d in deltas)

    if not changed:
        # The no-op path. No event, no write, and no pretence that a decision
        # was made — "/posture <the current one>" is a look, not a choice.
        return Selection(
            requested=requested,
            previous=previous,
            posture=previous,
            changed=False,
            applied=False,
            logged=False,
            deltas=deltas,
            actor=actor,
            session_id=session_id,
        )

    widening = bool(known.get(requested, {}).get(POSTURE_EXPLICIT_KEY))
    event_id, log_error = _record_intent(
        requested=requested,
        previous=previous,
        deltas=deltas,
        actor=actor,
        session_id=session_id,
        correlation_id=correlation_id,
        tenant_id=tenant_id,
        classification=classification,
        appender=appender,
    )

    if log_error and widening:
        logger.error(
            "posture_selection: refusing to widen to %r for %r — the event log is "
            "unreachable (%s), and there is no unaudited widening",
            requested, actor or "unknown", log_error,
        )
        return Selection(
            requested=requested,
            previous=previous,
            posture=previous,
            changed=True,
            applied=False,
            logged=False,
            deltas=deltas,
            refused=REFUSED_UNAUDITED_WIDENING,
            reason=(
                f"Posture {requested!r} widens the blast radius and the event log "
                f"could not record the decision ({log_error}). Refused."
            ),
            actor=actor,
            session_id=session_id,
        )

    # Apply. One variable: the four knobs follow through hcx-post-01's chain,
    # and a knob pinned above that layer stays where it is (reported, not
    # overwritten — see the module docstring).
    env[ENV_POSTURE] = requested

    if log_error:
        logger.warning(
            "posture_selection: %r selected posture %r but the decision was NOT "
            "recorded (%s) — agent_session_events has a hole here",
            actor or "unknown", requested, log_error,
        )
    else:
        logger.info(
            "posture_selection: %r selected posture %r (was %r); %d knob(s) moved, "
            "%d pinned above the posture layer",
            actor or "unknown", requested, previous,
            sum(1 for d in deltas if d.changed),
            sum(1 for d in deltas if d.pinned),
        )

    return Selection(
        requested=requested,
        previous=previous,
        posture=requested,
        changed=True,
        applied=True,
        logged=not log_error,
        deltas=deltas,
        reason=log_error,
        event_id=event_id,
        actor=actor,
        session_id=session_id,
    )


def _record_intent(
    *,
    requested: str,
    previous: str,
    deltas: Sequence[KnobDelta],
    actor: str,
    session_id: str,
    correlation_id: str,
    tenant_id: Optional[str],
    classification: Optional[str],
    appender: Optional[Callable[..., Any]],
) -> "tuple[str, str]":
    """Append the ``permission_posture`` event. Returns ``(event_id, error)``.

    The only place this module catches. ``event_log.append`` raises by design and
    that stays true — a writer that swallows its own INSERT is how
    ``module_budget_usage`` reported success while holding zero rows. The
    swallow lives here, once, and its outcome is *returned* rather than
    discarded, because the caller's next decision (refuse, or apply and warn)
    depends on it.
    """
    if not session_id:
        return "", "no session id — the event log is keyed by chat context"

    payload = {
        "posture": requested,
        "previous_posture": previous,
        "actor": actor or "unknown",
        "selected_at": datetime.now(timezone.utc).isoformat(),
        # The resolved values, not the declared ones: this is what the run
        # actually operates under, which is the only version worth auditing.
        "knobs": {d.knob: d.after for d in deltas},
        "declared": {d.knob: d.declared for d in deltas},
        "changes": [d.to_dict() for d in deltas if d.changed],
        # Recorded explicitly so the log answers "why does approval_mode not
        # match the posture" without a reader having to reconstruct the
        # precedence chain from four environment variables that are long gone.
        "pinned": {d.knob: d.pinned_by for d in deltas if d.pinned},
    }

    try:
        if appender is None:
            from tools.agent_runtime.event_log import append as _append

            write = _append
        else:
            write = appender
        event = write(
            session_id,
            EVENT_TYPE,
            payload,
            correlation_id=correlation_id,
            tenant_id=tenant_id,
            classification=classification,
        )
    except Exception as exc:  # noqa: BLE001 — returned to the caller, not dropped
        return "", str(exc)
    return str(getattr(event, "event_id", "") or ""), ""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _render(report: dict[str, Any]) -> str:
    lines = [
        f"posture   : {report['posture']} (selected by: {report['source']})",
        f"file      : {report['postures_path'] or '(none — built-in postures)'}",
        "knobs     :",
    ]
    for key, value in report["knobs"].items():
        note = ""
        holder = report["pinned"].get(key)
        if holder:
            note = f"   <- pinned by {holder}, not by the posture"
        lines.append(f"  {key:<22} {value!r}{note}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Show or select the agent permission posture (hcx-post-02). "
            "Selecting one appends a permission_posture event to "
            "agent_session_events recording who chose it."
        )
    )
    parser.add_argument("--select", metavar="POSTURE", help="Select this posture.")
    parser.add_argument("--session", default="", help="Chat context id to file the event under.")
    parser.add_argument("--actor", default="", help="Who is choosing (recorded verbatim).")
    parser.add_argument("--list", action="store_true", help="List selectable postures.")
    parser.add_argument("--json", action="store_true", help="JSON output.")
    args = parser.parse_args(argv)

    if args.list:
        postures = available_postures()
        if args.json:
            print(json.dumps(postures, indent=2, sort_keys=True, default=str))
            return 0
        for name, meta in postures.items():
            flag = "  [explicit selection only]" if meta["requires_explicit_selection"] else ""
            print(f"{name}{flag}")
            if meta["description"]:
                print(f"  {meta['description']}")
        return 0

    if args.select:
        result = select_posture(
            args.select,
            actor=args.actor or os.environ.get("USER") or os.environ.get("USERNAME") or "",
            session_id=args.session,
        )
        if args.json:
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True, default=str))
        else:
            print(result.summary())
        return 1 if result.refused else 0

    report = describe()
    print(json.dumps(report, indent=2, sort_keys=True, default=str) if args.json else _render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
