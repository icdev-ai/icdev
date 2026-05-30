# CUI // SP-CTI
"""CLI for cross-session coordination — usable by ANY agent (LLM-agnostic).

    python -m tools.coordination status                  # sessions + leases overview
    python -m tools.coordination whoami                  # this session's id/agent
    python -m tools.coordination register --intent "..." # register/refresh this session
    python -m tools.coordination heartbeat [--intent ..] # refresh heartbeat
    python -m tools.coordination end                     # mark this session ended
    python -m tools.coordination sessions [--json]       # list active sessions
    python -m tools.coordination reap                    # drop stale sessions
    python -m tools.coordination lease-acquire <res> [--intent ..] [--block] [--ttl N]
    python -m tools.coordination lease-release <res>
    python -m tools.coordination leases [--json]         # list held leases

A non-Claude agent coordinates by setting ICDEV_SESSION_ID (+ optional
ICDEV_AGENT) and calling these — no Claude hooks required.
"""
from __future__ import annotations

import argparse
import json
import sys

from tools.coordination import leases as _leases
from tools.coordination import session_registry as _reg
from tools.coordination.constants import get_agent_type, get_session_id


def _print(obj, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, default=str))
    else:
        if isinstance(obj, list):
            for item in obj:
                print("  " + json.dumps(item, default=str))
        else:
            for k, v in (obj.items() if isinstance(obj, dict) else []):
                print(f"  {k}: {v}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tools.coordination")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("whoami")
    sub.add_parser("status")
    r = sub.add_parser("register"); r.add_argument("--intent", default=None)
    h = sub.add_parser("heartbeat"); h.add_argument("--intent", default=None)
    sub.add_parser("end")
    s = sub.add_parser("sessions"); s.add_argument("--json", action="store_true")
    sub.add_parser("reap")
    la = sub.add_parser("lease-acquire")
    la.add_argument("resource"); la.add_argument("--intent", default="")
    la.add_argument("--block", action="store_true"); la.add_argument("--ttl", type=int, default=None)
    lr = sub.add_parser("lease-release"); lr.add_argument("resource")
    ll = sub.add_parser("leases"); ll.add_argument("--json", action="store_true")

    args = p.parse_args(argv)
    cmd = args.cmd or "status"

    if cmd == "whoami":
        _print({"session_id": get_session_id(), "agent_type": get_agent_type()}, False)
    elif cmd == "register":
        _print(_reg.register(args.intent), False)
    elif cmd == "heartbeat":
        print("  heartbeat:", _reg.heartbeat(args.intent))
    elif cmd == "end":
        print("  ended:", _reg.end_session())
    elif cmd == "sessions":
        _print(_reg.list_active(), getattr(args, "json", False))
    elif cmd == "reap":
        print("  reaped:", _reg.reap_stale())
    elif cmd == "lease-acquire":
        lease = _leases.acquire(args.resource, intent=args.intent, ttl_seconds=args.ttl, block=args.block)
        if lease is None:
            who = _leases.holder(args.resource)
            print(f"  DENIED — held by {who.get('holder_session') if who else '?'} "
                  f"({who.get('holder_agent') if who else '?'})")
            return 1
        if lease.prior_holder:
            print(f"  WARNING — {args.resource} also claimed by "
                  f"{lease.prior_holder.get('holder_session')} ({lease.prior_holder.get('holder_agent')}): "
                  f"{lease.prior_holder.get('intent')}")
        print(f"  acquired {args.resource} for {get_session_id()}")
    elif cmd == "lease-release":
        print("  released:", _leases.release(args.resource))
    elif cmd == "leases":
        _print(_leases.list_leases(), getattr(args, "json", False))
    elif cmd == "status":
        sessions = _reg.list_active()
        held = _leases.list_leases()
        print(f"Coordination status — me={get_session_id()} ({get_agent_type()})")
        print(f"Active sessions: {len(sessions)}")
        for s in sessions:
            mark = " (me)" if s.get("session_id") == get_session_id() else ""
            print(f"  - {s.get('session_id')} [{s.get('agent_type')}]{mark} "
                  f"intent={s.get('current_intent')!r} hb={s.get('last_heartbeat')}")
        print(f"Held leases: {len(held)}")
        for lz in held:
            print(f"  - {lz.get('resource')} <- {lz.get('holder_session')} "
                  f"({lz.get('holder_agent')}) intent={lz.get('intent')!r}")
    else:
        p.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
