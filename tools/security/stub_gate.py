#!/usr/bin/env python3
# CUI // SP-CTI
"""Zero-Trust stub gate — single source of truth for stub opt-in semantics.

Zero-trust device-trust and PDP integration adapters ship with *stubs* that
stand in for a live vendor API (CrowdStrike Falcon, DISA ICAM, Zscaler ZPA)
until real credentials are configured. Historically those stubs **failed
open** — fabricating a permissive result (healthy device / permit decision)
whenever the live API was unavailable. In production that is a security
defect: an unreachable PDP or EDR must default to *deny*, not *permit*.

This module centralises the one policy question those adapters need to ask:

    "Are stub results allowed to be honored in this environment?"

Production leaves ``ICDEV_ZT_ALLOW_STUB`` unset, so ``stub_allowed()`` returns
False and every stub path fails **closed** (deny / device status unknown).
Dev, CI, and e2e set ``ICDEV_ZT_ALLOW_STUB=1`` to opt back into the permissive
stub behavior so local workflows keep functioning without live vendor APIs.

Keeping the truthy-value semantics in exactly one place avoids each adapter
re-implementing (and subtly disagreeing about) what "enabled" means.

NIST 800-53: AC-3, IA-3, SA-9, ZTA Pillars 2 & 5
"""
from __future__ import annotations

import os

#: Env var that opts into honoring zero-trust stub results (fail-open).
STUB_ENV_VAR = "ICDEV_ZT_ALLOW_STUB"

_TRUTHY = ("1", "true", "yes")


#: Audit event type for every zero-trust decision that consults this gate.
#: ONE type with the surface namespaced in ``action``, following the
#: ``migration_canvas`` precedent rather than one type per adapter. Admitted to
#: ``audit_trail`` by migration 20260902213000 (rmf-zt-01) — an event type the
#: deployed CHECK does not admit is rejected on its first line and every
#: caller's best-effort ``except`` hides it.
AUDIT_EVENT_TYPE = "zt.stub_gate"


def stub_status() -> dict:
    """What this deployment's stub posture IS, in a shape a template can render.

    ``enabled`` is the gate; ``banner`` is None when it is off, so a surface can
    show the warning only while the stub is actually being honored. A page that
    renders nothing here is NOT asserting the deployment is live — it is
    asserting the gate is closed, which is the default.
    """
    enabled = stub_allowed()
    return {
        "enabled": enabled,
        "env_var": STUB_ENV_VAR,
        "banner": (
            "DEVICE POSTURE IS STUBBED — %s is set. Device trust, compliance "
            "scores and PDP decisions on this deployment are NOT evidence of "
            "the live estate." % STUB_ENV_VAR
        )
        if enabled
        else None,
    }


def record_stub_decision(component: str, subject: str, honored: bool,
                         detail: dict | None = None) -> dict:
    """Audit one zero-trust decision that consulted the stub gate.

    BOTH LEGS ARE RECORDED. A surface that logs only the permit can answer "was
    this permitted?" and never "was this evaluated?", and the refusal leg is the
    one an assessor asks about — it is the evidence the control fired.

    Best-effort but NEVER SILENT: the return value says whether the row landed,
    and the caller carries it on its own result. An audit write that fails and
    reports nothing is indistinguishable from one that never ran, which is the
    defect this whole module exists to refuse. It does not raise, because a
    canvas scan that dies when ``audit_trail`` is unreachable trades a recorded
    problem for an unrecorded outage.
    """
    payload = {
        "component": component,
        "subject": subject,
        "honored": bool(honored),
        "env_var": STUB_ENV_VAR,
    }
    payload.update(detail or {})
    action = "%s.%s" % (component, "stub_honored" if honored else "fail_closed")
    try:
        from tools.audit.audit_logger import log_event

        entry_id = log_event(
            event_type=AUDIT_EVENT_TYPE,
            actor=component,
            action=action,
            details=payload,
            classification="CUI",
        )
        return {"recorded": entry_id > 0, "entry_id": entry_id, "action": action}
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        return {"recorded": False, "entry_id": None, "action": action, "error": str(exc)}


def stub_allowed() -> bool:
    """Return True iff zero-trust stubs may be honored in this environment.

    Reads ``ICDEV_ZT_ALLOW_STUB`` and returns True only when it is set to a
    truthy value ("1", "true", "yes"; case-insensitive). Unset, empty, or any
    other value returns False — i.e. **fail closed** by default so that an
    unavailable live adapter denies rather than fabricating a permissive
    result.
    """
    val = os.environ.get(STUB_ENV_VAR, "").strip().lower()
    return val in _TRUTHY
