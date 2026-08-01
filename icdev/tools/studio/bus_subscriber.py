# CUI // SP-CTI
"""Studio ← cross-canvas event bus subscriber (dwo-evt-01-d5).

``studio_event_sources`` accepts a ``canvas_bus`` kind, and ``event_sources``
knows how to match an event against triggers — but nothing connected the two,
so a ``canvas_bus`` source was inert: a canvas could publish all day and no
workflow would ever start.

This module is that wire, in the shape every other canvas already uses
(``tools/*/bus_subscriber.py``, migration ``039_canvas_events``): a ``register()``
called once at blueprint init that installs in-process listeners on
``tools.canvas.event_bus``.

What a ``canvas_bus`` source's ``config_json`` declares
------------------------------------------------------
::

    {"canvas_id": "qdc",                       # required — whose events to hear
     "event_types": ["qdc.gate.executed"],     # optional — default ["*"] (all)
     "project_id": "default"}                  # optional — project for started runs

One listener is installed per (canvas_id, event_type) pair, bound to that
source.  When the bus fires it, the payload goes straight to
:func:`tools.studio.event_sources.dispatch_event`, which evaluates it with
``match_event`` and records every candidate trigger — matched or not — in the
append-only ``studio_trigger_events``.  No second matcher, no second audit.

A source with no ``canvas_id`` is skipped with a warning rather than silently
subscribing to nothing: an event source that can never receive an event is a
bug, not a configuration.

CLI::

    python tools/studio/bus_subscriber.py --list --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)

#: Installed listeners, as (source_id, canvas_id, event_type).  Registration is
#: idempotent so a second register() — after a new source is created at runtime
#: — does not double-fire every existing subscription.
_REGISTERED: set[tuple[str, str, str]] = set()


def _subscriptions_for(source: dict) -> list[tuple[str, str]]:
    """The (canvas_id, event_type) pairs a canvas_bus source listens on."""
    config = source.get("config") or {}
    canvas_id = str(config.get("canvas_id") or config.get("canvas") or "").strip()
    if not canvas_id:
        return []
    raw = config.get("event_types") or config.get("event_type") or ["*"]
    if isinstance(raw, str):
        raw = [raw]
    types = [str(t).strip() for t in raw if str(t).strip()]
    return [(canvas_id, t) for t in (types or ["*"])]


def _make_handler(source_id: str, project_id: str):
    """Build the bus handler for one source.

    The bus calls handlers as ``(event_id, canvas_id, event_type, payload)`` and
    swallows their exceptions, so this logs its own failures — otherwise a
    broken dispatch would be invisible.
    """

    def _handler(event_id: str, canvas_id: str, event_type: str, payload: dict) -> None:
        from tools.studio.event_sources import dispatch_event  # noqa: PLC0415

        body = dict(payload or {})
        # Correlation back to the canvas_events row, so a run started by a bus
        # event can be traced to the publish that caused it.  Recorded in the
        # studio_trigger_events payload alongside the canvas payload.
        body["_bus_event_id"] = event_id
        body["_canvas_id"] = canvas_id
        try:
            result = dispatch_event(
                source_id, event_type, body, project_id=project_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "studio.bus: dispatch failed for source %s event %s: %s",
                source_id, event_type, exc,
            )
            return
        if result.get("matched"):
            logger.info(
                "studio.bus: %s/%s started %d run(s) via source %s",
                canvas_id, event_type, result["matched"], source_id,
            )

    return _handler


def register_source(source: dict) -> int:
    """Install listeners for one ``canvas_bus`` source.  Returns pairs wired.

    Safe to call again for the same source — already-installed pairs are
    skipped.  Called by ``create_event_source`` so a source created from the
    Triggers panel is live immediately, not after the next restart.
    """
    source_id = source.get("source_id") or ""
    if not source_id or source.get("kind") != "canvas_bus":
        return 0
    if not source.get("enabled", 1):
        return 0

    pairs = _subscriptions_for(source)
    if not pairs:
        logger.warning(
            "studio.bus: canvas_bus source %s (%s) declares no canvas_id — "
            "it can never receive an event",
            source_id, source.get("name", ""),
        )
        return 0

    from tools.canvas.event_bus import subscribe  # noqa: PLC0415

    config = source.get("config") or {}
    project_id = str(config.get("project_id") or "default")
    handler = _make_handler(source_id, project_id)

    wired = 0
    for canvas_id, event_type in pairs:
        key = (source_id, canvas_id, event_type)
        if key in _REGISTERED:
            continue
        subscribe(canvas_id, event_type, handler)
        _REGISTERED.add(key)
        wired += 1
    return wired


def register() -> dict:
    """Wire every enabled ``canvas_bus`` event source onto the bus.

    Call once at blueprint init.  Returns ``{"sources", "subscriptions", "new"}``
    — ``subscriptions`` is how many listeners those sources have in total and
    ``new`` how many this call added, so a caller can see what is actually wired
    instead of assuming it worked.  A source created at runtime is already wired
    by ``create_event_source``, which is why ``new`` can legitimately be 0 while
    ``subscriptions`` is not.
    """
    try:
        from tools.studio.event_sources import list_event_sources  # noqa: PLC0415

        sources = [
            s for s in list_event_sources(enabled_only=True)
            if s.get("kind") == "canvas_bus"
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("studio.bus: could not read event sources: %s", exc)
        return {"sources": 0, "subscriptions": 0, "new": 0}

    wired = 0
    for source in sources:
        try:
            wired += register_source(source)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "studio.bus: could not wire source %s: %s",
                source.get("source_id"), exc,
            )

    source_ids = {s.get("source_id") for s in sources}
    total = len([k for k in _REGISTERED if k[0] in source_ids])
    if wired:
        logger.info(
            "studio.bus: %d new subscription(s) across %d canvas_bus source(s)",
            wired, len(sources),
        )
    return {"sources": len(sources), "subscriptions": total, "new": wired}


def list_subscriptions() -> list[dict]:
    """The listeners currently installed — "is my source actually wired?"."""
    return [
        {"source_id": s, "canvas_id": c, "event_type": t}
        for s, c, t in sorted(_REGISTERED)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Studio canvas_bus event source subscriptions"
    )
    parser.add_argument("--list", action="store_true", help="Register, then list subscriptions")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    if not args.list:
        parser.print_help()
        return 1

    summary = register()
    result = {**summary, "subscriptions_detail": list_subscriptions()}
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
