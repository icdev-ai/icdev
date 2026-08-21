"""MCP Tool Executor — generic Studio workflow step.

Dispatches any tool declared in ``tools/mcp/tool_registry.py::TOOL_REGISTRY``
so a workflow step can reach every registered tool without a hand-written
executor per integration.

Dispatch is **in-process**: the registry entry's ``module`` is imported with
``importlib`` and its ``handler`` called directly. No MCP server is started and
no stdio transport is opened — this is the same lazy-import path
``mcp/unified_server.py::_resolve_handler`` uses, minus the protocol layer.

Usage::

    python tools/studio/executors/mcp_executor.py --tool health_check --params '{}'

Contract (matches the runner's expectations, see workflow_runner._exec_step):
  stdout  = single-line JSON object
  exit 0  = handler ran and returned
  exit 1  = refused by the gate, unknown tool, invalid params, or handler raised

One deliberate divergence from the MCP protocol layer: unified_server catches a
raising handler and returns ``{"error": ...}`` as a *successful* tool call. Here
that exits 1, because a step whose handler blew up must fail the run rather than
pass a success record with an error buried in the payload.

Authorization (dwo-mcp-02)
--------------------------
Every dispatch passes the ``mcp_workflow_tools`` allowlist in
``args/security_gates.yaml`` (gate MCP-WF-001) before the registry is touched,
so a refused tool is never imported and its handler never loaded. The policy is
**default-deny**: a tool runs only if it is named in ``allowed``. Anything else
raises :class:`MCPWorkflowGateError`.

The gate is fail-closed — a missing, unparseable, or non-default-deny policy
refuses every tool rather than dispatching unchecked. There is deliberately no
bypass argument: ``run()`` is the only dispatch path and it always gates.

IL and RBAC limits (dwo-mcp-02-d3)
----------------------------------
An allowlisted tool is then checked against the caller: the caller's impact
level must meet the tool's ``min_il``, and the caller must hold a role the tool
requires. Limits are **not** restated in the gates file — they come from
``args/component_registry.yaml`` (``min_il`` / ``default_roles``) via the
component that owns the tool's handler module, so the workflow surface and the
HTTP canvas gate enforce one policy. Role checks fall back to an explicit
``canvas_access`` grant before refusing. A tool no component owns runs at the
platform baseline (IL4, no role limit).

Scope of the RBAC half today: none of the 29 tools currently on the allowlist
live inside a canvas package (they are all ``tools.mcp.*`` servers), so no role
limit applies to them yet — the check goes live the moment a canvas-owned tool
is allowlisted. The IL half is live now: a run whose caller context declares
IL2 cannot dispatch an IL4 platform tool.

Human approval gate (dwo-mcp-02-d4)
-----------------------------------
A tool on the ``requires_approval`` list is dispatchable, but only behind an
approved human gate in the same run. Immediately before dispatch — after the
allowlist, the caller's IL/roles and the parameter schema, so nobody is woken
to approve a call that would have been refused anyway — the executor parks a
gate and blocks on it.

The gate reuses the HITL infrastructure as-is: it is a
``studio_workflow_run_steps`` row with no tool path and status
``awaiting_approval``, which is exactly what an authored ``node_type: human``
step writes. ``workflow_runner.approve_step()`` / ``reject_step()`` /
``get_pending_approvals()``, the workflow Details modal, the Telegram listener
and the resume surface therefore all act on it unchanged — no new flag, no new
table, no second approval vocabulary.

One gate per (run, tool), found-or-created: a resumed run re-attaches to the
gate an approver was already shown rather than opening a second one beside it.
Approval dispatches; rejection refuses with ``mcp_tool_approval_rejected``;
an undecided gate refuses with ``mcp_tool_awaiting_human_approval`` and stays
parked, so resuming the run picks the decision up. A dispatch with no run to
park a gate on, or an unreachable gate store, refuses with
``mcp_tool_approval_gate_unavailable`` — fail-closed in every direction.

Append-only audit (dwo-mcp-02-d5)
---------------------------------
Every attempt writes exactly one row to ``studio_mcp_dispatch_audit``, on all
three dispatch paths — allowed, refused (by any gate, by an unknown tool, by
bad params, or by a handler that blew up) and parked awaiting a human decision.
Each row carries the tool, a SHA-256 digest of the parameters, the run and step,
the actor (principal, tenant, IL, roles, and where that identity came from), the
decision, the machine-readable reason, and a timestamp.

Parameters are digested, never stored verbatim: tool arguments routinely carry
CUI and credentials, and the audit question — "were these the same arguments the
approver saw" — a digest answers without widening the audit store's blast
radius. The row's classification comes from
``classification_manager.get_classification_for_il`` applied to the caller's
impact level, so an IL6 dispatch is marked SECRET rather than banner-stamped CUI.

The write is best-effort and never decides the dispatch: an unreachable audit
store must not turn a legitimately approved deployment into a failure, and it
must certainly not turn a *refusal* into a pass. The outcome is reported in the
step payload (``audit_written`` / ``audit_skipped``) so a silent audit outage is
visible rather than assumed.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Reserved key prefix for step results in run memory (dwo-mem-01).
MEMORY_KEY_PREFIX = "step:"

_MAX_SUGGESTIONS = 5

# ── Authorization gate (dwo-mcp-02, gate MCP-WF-001) ───────────────────────

GATES_FILENAME = "security_gates.yaml"

#: Top-level key holding the workflow allowlist inside the gates file.
GATE_POLICY_KEY = "mcp_workflow_tools"

#: Parsed policies, keyed by the path they came from. Cleared by ``refresh=True``.
_POLICY_CACHE: dict[str, dict] = {}


class MCPWorkflowGateError(RuntimeError):
    """A tool was refused by the MCP workflow allowlist, or the policy is unusable.

    ``reason`` carries the MCP-WF-001 block condition so the CLI can report it
    as ``error_type`` and d5 can audit it without re-parsing the message.
    """

    def __init__(
        self,
        message: str,
        *,
        tool: str = "",
        reason: str = "",
        step_run_id: str = "",
    ):
        super().__init__(message)
        self.tool = tool
        self.reason = reason
        #: Gate this refusal is parked on, when there is one (d4). Carried so an
        #: operator can approve it from the step's stdout alone.
        self.step_run_id = step_run_id


def _candidate_gate_paths() -> list[Path]:
    """Gate-file locations to probe, nearest ancestor first.

    Both ``<root>/args/`` and ``<root>/data/args/`` are probed at every level so
    this resolves from the repo checkout, from the ``icdev/`` package mirror, and
    from a pip-installed wheel where the file ships as package data. Mirrors the
    strategy in ``tools/config/component_registry.py::_find_repo_root``.
    """
    here = Path(__file__).resolve()
    paths: list[Path] = []
    for parent in here.parents:
        for rel in (("args",), ("data", "args")):
            candidate = parent.joinpath(*rel, GATES_FILENAME)
            if candidate not in paths:
                paths.append(candidate)
    return paths


def _parse_policy(path: Path, key: str = GATE_POLICY_KEY) -> dict | None:
    """Return the ``key`` policy section of ``path``, or None if absent/unreadable.

    None means "keep looking" — several gate files exist in a checkout and only
    the authoritative one declares this section. Never returns a policy that is
    not default-deny: an edited ``default`` raises rather than being ignored,
    because silently enforcing a stricter rule than the file states hides the
    edit from whoever made it.

    ``key`` is a parameter because the agent surface (hgx-agent-02) declares its
    own default-deny section, ``agent_workflow_tools``, in the same file and is
    loaded by the same fail-closed reader rather than by a second one.
    """
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, yaml.YAMLError):
        return None

    policy = data.get(key) if isinstance(data, dict) else None
    if not isinstance(policy, dict):
        return None

    default = str(policy.get("default", "")).strip().lower()
    if default != "deny":
        raise MCPWorkflowGateError(
            f"'{key}.default' is {default or '(unset)'!r} in {path}, "
            f"expected 'deny'. This executor implements a default-deny allowlist "
            f"only and will not guess what the edited policy permits.",
            reason="gate_policy_unavailable",
        )
    return policy


def load_gate_policy(
    path: str | Path | None = None, *, refresh: bool = False, key: str = GATE_POLICY_KEY
) -> dict:
    """Return the ``mcp_workflow_tools`` policy (or another default-deny section).

    Args:
        path: Read this gate file instead of probing for one.
        refresh: Bypass the cache and re-read from disk.
        key: Top-level section to read. Defaults to :data:`GATE_POLICY_KEY`; the
            agent surface passes ``agent_workflow_tools``.

    Raises:
        MCPWorkflowGateError: if no readable default-deny policy is found. The
            gate is fail-closed: without a policy nothing dispatches.
    """
    candidates = [Path(path)] if path else _candidate_gate_paths()
    # Keyed by section as well as path: two sections of the same file are two
    # policies, and caching them under one key would serve the wrong allowlist.
    cache_key = f"{key}@{candidates[0]}" if path else key
    if not refresh and cache_key in _POLICY_CACHE:
        return _POLICY_CACHE[cache_key]

    for candidate in candidates:
        policy = _parse_policy(candidate, key)
        if policy is not None:
            policy = {**policy, "_source": str(candidate)}
            _POLICY_CACHE[cache_key] = policy
            return policy

    raise MCPWorkflowGateError(
        f"Cannot enforce the workflow tool allowlist: no '{key}' "
        f"section found in any {GATES_FILENAME} (looked in "
        f"{', '.join(str(p.parent) for p in candidates[:4])}), or PyYAML is not "
        f"installed. Refusing to dispatch — the gate is fail-closed.",
        reason="gate_policy_unavailable",
    )


def _tool_set(policy: dict, key: str) -> frozenset[str]:
    """Return one of the policy's tool lists as a set, tolerating null/absent."""
    return frozenset(str(t) for t in (policy.get(key) or []))


def allowed_tools(policy: dict | None = None) -> frozenset[str]:
    """Tools dispatchable from a workflow step with no human gate."""
    return _tool_set(policy if policy is not None else load_gate_policy(), "allowed")


def approval_tools(policy: dict | None = None) -> frozenset[str]:
    """Tools that need an approved human gate before dispatch (reachable in d4)."""
    return _tool_set(
        policy if policy is not None else load_gate_policy(), "requires_approval"
    )


#: Disposition returned by :func:`check_tool_allowed` for a tool that
#: dispatches unattended.
DISPOSITION_ALLOWED = "allowed"

#: Disposition for a tool that dispatches only behind an approved human gate.
DISPOSITION_REQUIRES_APPROVAL = "requires_approval"


def check_tool_allowed(tool: str, policy: dict | None = None) -> str:
    """Refuse ``tool`` unless the allowlist names it; return how it dispatches.

    Returns ``'allowed'`` (dispatch unattended) or ``'requires_approval'``
    (dispatch only after :func:`await_approval` clears a human gate). Passing
    this check is therefore necessary but not sufficient — a
    ``requires_approval`` tool is still undispatchable until its gate is
    approved, and ``run()`` is the only path that dispatches at all.

    Raises:
        MCPWorkflowGateError: always names the tool, so the refusal is
            actionable from the step's stdout alone.
    """
    policy = policy if policy is not None else load_gate_policy()

    if tool in allowed_tools(policy):
        return DISPOSITION_ALLOWED

    if tool in approval_tools(policy):
        return DISPOSITION_REQUIRES_APPROVAL

    # Suggest from the allowlist, not the registry: a typo of an allowlisted
    # tool is the common case, and naming it costs no registry import.
    close = _closest(tool, sorted(allowed_tools(policy)))
    hint = f" Closest allowlisted tools: {', '.join(close)}." if close else ""
    raise MCPWorkflowGateError(
        f"MCP tool '{tool}' is not allowlisted for workflow steps. The "
        f"{GATE_POLICY_KEY} policy is default-deny: add '{tool}' to its "
        f"'allowed' list in {GATES_FILENAME} (read-only tools only) or to "
        f"'requires_approval' (state-changing tools) to make it dispatchable."
        + hint,
        tool=tool,
        reason="mcp_tool_not_allowlisted",
    )


# ── Human approval gate (dwo-mcp-02-d4, gate MCP-WF-001) ───────────────────
#
# A `requires_approval` tool parks on the *existing* HITL representation: a
# `studio_workflow_run_steps` row with no tool path and status
# `awaiting_approval` is exactly what a `node_type: human` step already writes.
# So `workflow_runner.approve_step()` / `reject_step()` /
# `get_pending_approvals()`, the Details modal, the Telegram listener and the
# resume surface all act on this gate unchanged — no new flag, no new table, no
# second approval vocabulary.

#: Step id prefix of the gate a ``requires_approval`` tool parks on. One gate
#: per (run, tool): the same tool dispatched twice in a run reuses the decision
#: rather than asking a second approver the same question.
APPROVAL_STEP_PREFIX = "approval:"

#: Seconds the executor waits for a decision before giving up. A gate that
#: outlives the wait stays parked in the database, so resuming the run
#: re-attaches to it (and dispatches immediately if it was decided meanwhile)
#: rather than opening a fresh one.
DEFAULT_APPROVAL_WAIT = 900.0

#: Seconds between re-reads of the gate row while waiting.
APPROVAL_POLL_SECONDS = 1.0

#: Env override for the wait window, for a deployment whose approvers are
#: slower (or a test that wants no wait at all).
APPROVAL_WAIT_ENV = "ICDEV_MCP_APPROVAL_WAIT"

#: The shared HITL vocabulary's terminal gate statuses.
_DECIDED = ("approved", "rejected")


def approval_step_id(tool: str, *, prefix: str = APPROVAL_STEP_PREFIX) -> str:
    """Step id of ``tool``'s gate within a run. Stable, so a resume re-attaches.

    ``prefix`` namespaces the gate by surface. The agent surface passes its own
    (``approval:agent:``) so that approving ``run_command`` for a reviewed mcp
    step cannot also authorize an agent loop to run whatever it likes — the two
    are different questions and must be two gates even in the same run.
    """
    return f"{prefix}{tool}"


def approval_wait_seconds(policy: dict | None = None) -> float:
    """How long to wait for a decision: env, then policy, then the default.

    Zero is legitimate and means "park the gate and do not block" — the gate is
    still created and still visible to approvers, the dispatch just fails now
    instead of later. Unparseable values fall through to the next source rather
    than raising, because a typo in an operational knob must not make a
    state-changing tool undispatchable in a way that reads as a policy refusal.
    """
    for raw in (
        os.environ.get(APPROVAL_WAIT_ENV),
        (policy or {}).get("approval_wait_seconds"),
    ):
        if raw is None or str(raw).strip() == "":
            continue
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            continue
    return DEFAULT_APPROVAL_WAIT


def _gate_connection():
    """Open the Studio database. Import is lazy so a refused tool never pays it."""
    from tools.db.storage import get_connection  # noqa: PLC0415

    return get_connection()


def _read_gate(conn, run_id: str, step_id: str) -> dict | None:
    """Latest gate row for (run, tool), or None when no gate has been opened."""
    row = conn.execute(
        "SELECT step_run_id, status, stderr FROM studio_workflow_run_steps "
        "WHERE run_id = %s AND step_id = %s ORDER BY started_at DESC LIMIT 1",
        (run_id, step_id),
    ).fetchone()
    return dict(row) if row else None


def _set_run_status(run_id: str, status: str) -> None:
    """Reflect a DECIDED gate on the run row (the un-park, `running`).

    Parking does NOT go through here — see :func:`open_approval_gate`, which
    writes the gate row and the run row in one transaction. This is the other
    direction: the gate has already been decided by somebody else's commit, so
    there is no pair to keep consistent and nothing to make atomic.

    Best-effort: this is visibility, not authorization, and a failure here must
    not decide the gate. The run's own worker rewrites this row when the run
    finishes either way.
    """
    try:
        conn = _gate_connection()
        try:
            conn.execute(
                "UPDATE studio_workflow_runs SET status = %s WHERE run_id = %s",
                (status, run_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — see docstring
        pass


def open_approval_gate(
    run_id: str,
    tool: str,
    *,
    prefix: str = APPROVAL_STEP_PREFIX,
    label: str = "Approve MCP tool",
) -> dict:
    """Return ``tool``'s gate in ``run_id``, creating a pending one if absent.

    Find-or-create, not create: a run resumed after a restart, or a tool
    dispatched twice, must re-attach to the gate an approver has already been
    shown rather than silently opening a second one beside it.

    The gate row and the run row are parked in ONE transaction (rem-hyg-19).
    They used to be two: this function committed the step row and
    :func:`_set_run_status` then committed the run row on its own connection,
    leaving an observable window in which the gate read ``awaiting_approval``
    while the run still read ``running``. hgx-park-01 closed exactly that window
    in ``workflow_runner._park_for_approval`` — the authored ``node_type: human``
    path — and this second park, the one a ``requires_approval`` MCP tool takes,
    was never made atomic. It surfaced as an intermittent
    ``assert 'running' == 'awaiting_approval'`` on the Windows runner, on two
    unrelated branches inside ninety minutes.

    The window is not only a test problem. Between the two commits the run
    reads ``running`` while a state-changing tool is in fact blocked on a human,
    so the Studio run list, the resume surface and anything else keying on the
    run status describe a run that is executing when it is parked.

    Making the run write part of the transaction also makes it load-bearing: if
    it fails, the whole park rolls back and no gate row is left behind, and
    :func:`await_approval` turns that into a fail-closed
    ``mcp_tool_approval_gate_unavailable`` refusal. That is the right direction
    — a park we could not record whole must not dispatch.

    Args:
        prefix: Gate-id namespace (see :func:`approval_step_id`).
        label: What the approver reads in the pending-approvals list. Names the
            surface, because "approve write_file" means something different for
            an authored step than for a loop that chose it.
    """
    import uuid  # noqa: PLC0415

    step_id = approval_step_id(tool, prefix=prefix)
    conn = _gate_connection()
    try:
        existing = _read_gate(conn, run_id, step_id)
        if existing and existing["status"] in _DECIDED:
            # Already decided: the run status is the un-park's business, not
            # the park's, so leave it alone.
            return existing

        if existing and existing["status"] == "awaiting_approval":
            gate = existing
        else:
            step_run_id = f"sr-{uuid.uuid4().hex[:12]}"
            conn.execute(
                """INSERT INTO studio_workflow_run_steps
                   (step_run_id, run_id, step_id, step_name, tool, status, started_at)
                   VALUES (%s, %s, %s, %s, '', 'awaiting_approval', %s)""",
                (
                    step_run_id,
                    run_id,
                    step_id,
                    f"{label}: {tool}",
                    _utcnow(),
                ),
            )
            gate = {
                "step_run_id": step_run_id,
                "status": "awaiting_approval",
                "stderr": "",
            }

        conn.execute(
            "UPDATE studio_workflow_runs SET status = 'awaiting_approval' "
            "WHERE run_id = %s",
            (run_id,),
        )
        # ONE commit for both rows: an observer sees the park whole, or not yet.
        conn.commit()
        return gate
    finally:
        conn.close()


def _utcnow() -> str:
    from datetime import datetime, timezone  # noqa: PLC0415

    return datetime.now(timezone.utc).isoformat()


def _poll_gate(
    run_id: str, step_id: str, wait_seconds: float, poll_seconds: float
) -> tuple[str, str]:
    """Block until the gate is decided or the window closes.

    Returns ``(status, reason)``; status is ``''`` on expiry. Polls the database
    rather than an in-process Event because the approver is in another process
    (the dashboard, the Telegram listener, the CLI) — the executor is a
    subprocess of the run, not of whoever approves.
    """
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            conn = _gate_connection()
            try:
                row = _read_gate(conn, run_id, step_id)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — a blip must not decide the gate
            row = None
        if row and row["status"] in _DECIDED:
            return row["status"], row.get("stderr") or ""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "", ""
        time.sleep(min(poll_seconds, remaining))


def await_approval(
    tool: str,
    run_id: str,
    *,
    wait_seconds: float | None = None,
    poll_seconds: float = APPROVAL_POLL_SECONDS,
    policy: dict | None = None,
    prefix: str = APPROVAL_STEP_PREFIX,
    label: str = "Approve MCP tool",
    surface: str = "MCP tool",
    policy_key: str = GATE_POLICY_KEY,
) -> dict:
    """Block dispatch of ``tool`` until a human approves its gate in ``run_id``.

    Returns the approval record on approval. Raises on rejection, on expiry,
    and when there is no run to park a gate on — the gate is fail-closed in
    every direction, including an unreachable gate store.

    Args:
        prefix / label: Gate namespace and approver-facing name (see
            :func:`open_approval_gate`).
        surface / policy_key: What the refusal messages call this tool and the
            policy section they point the reader at. The agent surface reuses
            this whole function rather than growing a parallel approval path;
            only the wording and the gate namespace differ.
    """
    step_id = approval_step_id(tool, prefix=prefix)
    if not run_id:
        raise MCPWorkflowGateError(
            f"{surface} '{tool}' is state-changing and dispatches only behind an "
            f"approved human gate ({policy_key}.requires_approval), but "
            f"this dispatch has no run to park one on (no --run-id / "
            f"ICDEV_RUN_ID). Run it as a workflow step so an approver can see "
            f"and decide the gate. Refusing — the gate is fail-closed.",
            tool=tool,
            reason="mcp_tool_approval_gate_unavailable",
        )

    try:
        gate = open_approval_gate(run_id, tool, prefix=prefix, label=label)
    except Exception as exc:  # noqa: BLE001
        raise MCPWorkflowGateError(
            f"{surface} '{tool}' requires a human gate, but the gate store is "
            f"unreachable ({type(exc).__name__}: {exc}). Refusing to dispatch "
            f"an unapproved state-changing tool — the gate is fail-closed.",
            tool=tool,
            reason="mcp_tool_approval_gate_unavailable",
        ) from exc

    step_run_id = gate["step_run_id"]
    status, reason = gate["status"], gate.get("stderr") or ""

    if status not in _DECIDED:
        wait = approval_wait_seconds(policy) if wait_seconds is None else max(0.0, wait_seconds)
        # The run already reads `awaiting_approval`: `open_approval_gate` parked
        # it in the SAME transaction that made the gate visible (rem-hyg-19).
        # Do not restore a `_set_run_status` call here — a second, separately
        # committed write is precisely the window that atomicity closed.
        status, reason = _poll_gate(run_id, step_id, wait, poll_seconds)
        if status == "approved":
            _set_run_status(run_id, "running")

    if status == "approved":
        return {
            "step_run_id": step_run_id,
            "step_id": step_id,
            "status": "approved",
            "decision_note": reason,
        }

    if status == "rejected":
        raise MCPWorkflowGateError(
            f"{surface} '{tool}' was refused by its human gate: "
            f"{reason or '(no reason given)'}. The step is denied, not failed — "
            f"re-running it will re-read this decision.",
            tool=tool,
            reason="mcp_tool_approval_rejected",
            step_run_id=step_run_id,
        )

    raise MCPWorkflowGateError(
        f"{surface} '{tool}' is waiting on human approval and nobody decided its "
        f"gate in time. The gate stays parked as step_run_id "
        f"'{step_run_id}' — approve or reject it (workflow Details modal, or "
        f"workflow_runner.approve_step/reject_step) and resume the run; the "
        f"resumed dispatch re-attaches to this gate rather than opening a new "
        f"one.",
        tool=tool,
        reason="mcp_tool_awaiting_human_approval",
        step_run_id=step_run_id,
    )


# ── Registry lookup ────────────────────────────────────────────────────────

def resolve_entry(tool: str) -> dict:
    """Return the TOOL_REGISTRY entry for ``tool``.

    Raises LookupError with the closest matching names when unknown.
    """
    from tools.mcp.tool_registry import RESOURCE_REGISTRY, TOOL_REGISTRY

    entry = TOOL_REGISTRY.get(tool)
    if entry:
        return entry

    if tool in RESOURCE_REGISTRY:
        raise LookupError(
            f"'{tool}' is an MCP resource, not a tool — this executor dispatches "
            f"TOOL_REGISTRY entries only"
        )

    raise LookupError(_unknown_tool_message(tool, list(TOOL_REGISTRY)))


def _closest(tool: str, names: list[str]) -> list[str]:
    """Return the names most likely meant by ``tool``, best first."""
    close = difflib.get_close_matches(tool, names, n=_MAX_SUGGESTIONS, cutoff=0.6)
    if not close:
        lowered = tool.lower()
        close = [n for n in names if lowered in n.lower()][:_MAX_SUGGESTIONS]
    return close


def _unknown_tool_message(tool: str, names: list[str]) -> str:
    """Build an unknown-tool error listing the closest registry names."""
    close = _closest(tool, names)
    msg = f"Unknown MCP tool '{tool}' ({len(names)} tools registered)"
    if close:
        msg += ". Closest matches: " + ", ".join(close)
    return msg


# ── Param validation ───────────────────────────────────────────────────────

def parse_params(raw: str) -> dict:
    """Parse the --params JSON string into a dict."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--params is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(
            f"--params must be a JSON object, got {type(value).__name__}"
        )
    return value


def validate_params(params: dict, schema: dict) -> list[str]:
    """Return a list of human-readable schema violations (empty == valid)."""
    if not schema:
        return []
    try:
        import jsonschema  # noqa: PLC0415
    except ImportError:
        return []  # validation is best-effort; dispatch still guarded by the handler

    validator = jsonschema.Draft7Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(params), key=lambda e: list(e.path)):
        field = ".".join(str(p) for p in err.path) or "(root)"
        errors.append(f"{field}: {err.message}")
    return errors


# ── Caller IL and RBAC limits (dwo-mcp-02-d3) ──────────────────────────────

#: Run-memory key holding the run's principal (see ``resolve_caller``).
CALLER_KEY = "caller"

#: Impact level assumed for a run that declares no caller. Matches the default
#: in ``canvas_access._has_sufficient_il``: an undeclared principal operates at
#: the deployment's own level, not at a privileged one.
DEFAULT_CALLER_IL = "IL4"

#: Impact level required by a tool that no registry component owns. ICDEV's
#: platform baseline is CUI/IL4, so a platform tool is treated as IL4 rather
#: than as unclassified.
DEFAULT_TOOL_MIN_IL = "IL4"

#: Environment fallbacks for the caller's IL, first match wins.
CALLER_IL_ENV = ("ICDEV_MCP_CALLER_IL", "ICDEV_IMPACT_LEVEL")

#: Environment fallback for the caller's roles (comma-separated).
CALLER_ROLES_ENV = "ICDEV_MCP_CALLER_ROLES"


def _il_order() -> dict:
    """Return the platform's impact-level ordering.

    Imported from ``canvas_access`` rather than restated: one ordering for the
    HTTP canvas gate and the workflow gate, so raising a canvas to IL5 cannot
    leave the workflow surface enforcing the old order.
    """
    try:
        from tools.security.canvas_access import _IL_ORDER  # noqa: PLC0415
    except ImportError as exc:
        raise MCPWorkflowGateError(
            f"Cannot evaluate impact-level limits: tools.security.canvas_access "
            f"is unimportable ({exc}). Refusing to dispatch — the gate is "
            f"fail-closed.",
            reason="gate_policy_unavailable",
        ) from exc
    return _IL_ORDER


def _normalize_roles(value) -> tuple[str, ...]:
    """Coerce a roles value (list, tuple, or comma-separated string) to a tuple."""
    if not value:
        return ()
    if isinstance(value, str):
        parts = value.split(",")
    else:
        parts = list(value)
    return tuple(str(p).strip() for p in parts if str(p).strip())


def read_caller_context(run_id: str) -> dict:
    """Return the run's declared caller from run memory, or ``{}``.

    Soft dependency, like :func:`write_run_memory`: a run whose trigger surface
    never wrote a ``caller`` key falls through to the environment defaults
    rather than failing. Absence is not treated as an error because no trigger
    surface writes this key yet.
    """
    if not run_id:
        return {}
    try:
        from tools.studio import run_memory  # noqa: PLC0415
    except ImportError:
        return {}
    try:
        value = run_memory.get(run_id, CALLER_KEY, default=None)
    except Exception:  # noqa: BLE001 — an unreadable memory must not fail open loudly
        return {}
    return value if isinstance(value, dict) else {}


def resolve_caller(run_id: str = "", overrides: dict | None = None) -> dict:
    """Resolve the principal a dispatch runs as.

    Resolution order, most specific first:

    1. ``overrides`` — the executor's ``--caller-*`` flags.
    2. Run memory's ``caller`` key — the workflow context (dwo-mem-01).
    3. ``ICDEV_MCP_CALLER_IL`` / ``ICDEV_IMPACT_LEVEL`` and
       ``ICDEV_MCP_CALLER_ROLES`` from the environment.
    4. :data:`DEFAULT_CALLER_IL` with no roles.

    Fields are resolved independently, so a run may declare an IL in memory and
    have its roles come from the environment.

    Returns:
        ``{"principal_id", "tenant_id", "impact_level", "roles", "source"}``.
        ``source`` names where the impact level came from, so a refusal can say
        which layer decided it.
    """
    overrides = {k: v for k, v in (overrides or {}).items() if v}
    context = read_caller_context(run_id)

    impact_level, source = "", ""
    for candidate, origin in (
        (overrides.get("impact_level"), "argument"),
        (context.get("impact_level"), f"run memory '{CALLER_KEY}'"),
    ):
        if candidate:
            impact_level, source = str(candidate), origin
            break
    if not impact_level:
        for name in CALLER_IL_ENV:
            if os.environ.get(name):
                impact_level, source = os.environ[name], f"${name}"
                break
    if not impact_level:
        impact_level, source = DEFAULT_CALLER_IL, "default (no caller declared)"

    roles = (
        _normalize_roles(overrides.get("roles"))
        or _normalize_roles(context.get("roles"))
        or _normalize_roles(os.environ.get(CALLER_ROLES_ENV))
    )

    return {
        "principal_id": str(
            overrides.get("principal_id") or context.get("principal_id") or ""
        ),
        "tenant_id": str(overrides.get("tenant_id") or context.get("tenant_id") or ""),
        "impact_level": impact_level.strip().upper(),
        "roles": roles,
        "source": source,
    }


def _owning_component(module_path: str, registry=None):
    """Return the registry component whose package contains ``module_path``.

    Ownership is by module package — ``tools.infra_canvas.foo`` is owned by the
    component whose ``module`` is ``tools.infra_canvas.blueprint``. The tool's
    ``category`` is deliberately *not* consulted: category names collide with
    component ``cli_name``s by coincidence (category ``infra`` vs. the
    Infrastructure canvas), and authorizing on a coincidental string match
    would deny tools for reasons nobody declared.

    Where several components share a package, the strictest (highest ``min_il``)
    wins, so an ambiguous mapping cannot resolve to the weaker of two policies.
    """
    if not module_path:
        return None
    if registry is None:
        from tools.config.component_registry import get_registry  # noqa: PLC0415

        registry = get_registry()

    order = _il_order()
    best, best_len, best_il = None, -1, -1
    for component in registry:
        module = component.module or ""
        if "." not in module:
            continue
        package = module.rsplit(".", 1)[0]
        if module_path != package and not module_path.startswith(package + "."):
            continue
        il = order.get((component.min_il or "").upper(), -1)
        if len(package) > best_len or (len(package) == best_len and il > best_il):
            best, best_len, best_il = component, len(package), il
    return best


def _registry_authorization(tool: str, entry: dict | None) -> dict:
    """The tool's own declaration in ``tools/mcp/tool_registry.py`` (exa-policy-07).

    Fail-CLOSED on an unimportable registry: this is the layer that used to
    contribute nothing, and silently reverting to "no limits" the day the import
    breaks would reintroduce exactly the hole the declarations closed.
    """
    from tools.mcp import tool_registry  # noqa: PLC0415 -- keeps import cost off load

    return tool_registry.tool_authorization(tool, entry=entry)


def tool_requirements(tool: str, entry: dict | None = None, registry=None) -> dict:
    """Return the IL and role limits ``tool`` is dispatched under.

    Two declarations are combined, and neither is optional:

    ``tools/mcp/tool_registry.py`` (exa-policy-07)
        The tool's OWN ``min_il`` and ``required_roles``, derived from its
        ``read_only`` flag, its mutating-bundle membership and its category,
        with per-tool overrides. Every MCP authorization surface reads this same
        declaration, so a tool cannot be cheaper through one surface than
        another. A tool with no declaration at all resolves restrictively
        (IL5 / admin) rather than to the baseline.

    ``args/component_registry.yaml``
        The ``min_il`` and ``default_roles`` of the component that OWNS the
        tool's handler module — the same limits the HTTP canvas gate enforces.

    The STRICTER impact level of the two wins. Roles do NOT merge: a component's
    ``default_roles`` REPLACES the registry declaration, because "hold any one of
    these" gets weaker as the set grows, and the component is the more specific
    claim (it names a canvas a principal can also be granted access to).

    Returns:
        ``{"min_il", "required_roles", "component", "component_name", "source",
        "roles_source", "tier"}``. ``source`` names where ``min_il`` was decided
        and ``roles_source`` where the roles were, so a refusal can say which
        declaration to go and edit.
    """
    entry = entry if entry is not None else resolve_entry(tool)
    declared = _registry_authorization(tool, entry)

    min_il = str(declared["min_il"]).upper()
    roles = tuple(declared["required_roles"])
    source = roles_source = declared["source"]

    component = _owning_component(str(entry.get("module", "") or ""), registry)
    if component is None:
        return {
            "min_il": min_il,
            "required_roles": roles,
            "component": "",
            "component_name": "",
            "source": source,
            "roles_source": roles_source,
            "tier": declared["tier"],
        }

    order = _il_order()
    component_il = (component.min_il or DEFAULT_TOOL_MIN_IL).strip().upper()
    # An UNRECOGNISED component level is adopted rather than compared away.
    # `order.get(x, -1)` would rank a typo below every real level and silently
    # keep the registry's, so a component declaring `min_il: IL7` would be
    # dispatched at IL4 instead of being refused. Carry it forward and let
    # check_caller_authorized refuse it — the gate does not guess.
    if component_il not in order or order[component_il] > order.get(min_il, -1):
        min_il = component_il
        source = f"component_registry:{component.key}"
    if component.default_roles:
        roles = tuple(component.default_roles)
        roles_source = f"component_registry:{component.key}"

    return {
        "min_il": min_il,
        "required_roles": roles,
        "component": component.key,
        "component_name": component.display_name or component.key,
        "source": source,
        "roles_source": roles_source,
        "tier": declared["tier"],
    }


def _has_canvas_grant(caller: dict, canvas_name: str) -> bool:
    """Return True if the caller holds an explicit grant on ``canvas_name``.

    Consulted only after the caller's declared roles fail to match, and only
    when the caller has an identity to look up: a direct or group grant is a
    legitimate way to reach a canvas without holding its default role, but it
    costs a DB round trip that an anonymous run cannot benefit from.
    """
    if not (caller.get("principal_id") and caller.get("tenant_id")):
        return False
    try:
        from tools.security.canvas_access import check_access  # noqa: PLC0415

        return bool(
            check_access(
                caller["principal_id"],
                caller["tenant_id"],
                canvas_name,
                required_level="read",
                user_role=(caller.get("roles") or ("",))[0],
            )
        )
    except Exception:  # noqa: BLE001 — an unreachable grant store denies, never crashes
        return False


def check_caller_authorized(
    tool: str,
    caller: dict | None = None,
    entry: dict | None = None,
    registry=None,
) -> dict:
    """Refuse ``tool`` unless the caller clears its IL and role limits.

    Args:
        tool: Registry tool name, already past the allowlist.
        caller: Resolved caller (see :func:`resolve_caller`). Defaults to a
            caller resolved with no run context.
        entry: The tool's registry entry, when already resolved.
        registry: Component registry to read limits from. Injectable for tests.

    Returns:
        The requirements the caller cleared, for the step payload and d5 audit.

    Raises:
        MCPWorkflowGateError: ``mcp_tool_exceeds_caller_il`` when the caller's
            impact level is below the tool's minimum (or is not a level this
            platform knows), ``mcp_tool_missing_required_role`` when the tool's
            owning component requires a role the caller neither holds nor has
            been granted.
    """
    caller = caller if caller is not None else resolve_caller()
    requirements = tool_requirements(tool, entry=entry, registry=registry)

    order = _il_order()
    required_il = str(requirements["min_il"]).upper()
    caller_il = str(caller.get("impact_level") or "").upper()
    required_rank = order.get(required_il)
    caller_rank = order.get(caller_il)

    if required_rank is None:
        raise MCPWorkflowGateError(
            f"MCP tool '{tool}' declares min_il {required_il!r} "
            f"({requirements['source']}), which is not a known impact level "
            f"({', '.join(sorted(order))}). Refusing to dispatch — the gate "
            f"will not guess what an unrecognized level permits.",
            tool=tool,
            reason="mcp_tool_exceeds_caller_il",
        )
    if caller_rank is None or caller_rank < required_rank:
        owner = (
            f" (owned by {requirements['component_name']})"
            if requirements["component"]
            else f" (declared in {requirements['source']}; no component owns it)"
        )
        detail = (
            f"caller impact level {caller_il!r} is not a known level "
            f"({', '.join(sorted(order))})"
            if caller_rank is None
            else f"caller is {caller_il}, tool requires {required_il}"
        )
        raise MCPWorkflowGateError(
            f"MCP tool '{tool}' requires impact level {required_il}{owner}, "
            f"but the caller cannot meet it: {detail}. Caller IL resolved from "
            f"{caller.get('source') or 'unknown'}. Raise the run's caller "
            f"context or dispatch this tool from an {required_il} run.",
            tool=tool,
            reason="mcp_tool_exceeds_caller_il",
        )

    required_roles = requirements["required_roles"]
    if required_roles:
        held = set(caller.get("roles") or ())
        if not held & set(required_roles) and not _has_canvas_grant(
            caller, requirements["component"]
        ):
            # The canvas-grant escape hatch only exists when a component owns
            # the tool; a registry-declared role has no canvas to be granted on,
            # so the message must not send an operator looking for one.
            remedy = (
                f"Grant the principal access to '{requirements['component']}' or "
                f"run the step as a principal that holds one of those roles."
                if requirements["component"]
                else "Run the step as a principal that holds one of those roles."
            )
            raise MCPWorkflowGateError(
                f"MCP tool '{tool}' requires one of these roles: "
                f"{', '.join(sorted(required_roles))} (declared in "
                f"{requirements['roles_source']}). The caller holds "
                f"{', '.join(sorted(held)) or '(no roles)'}"
                + (
                    " and has no explicit canvas_access grant. "
                    if requirements["component"]
                    else ". "
                )
                + remedy,
                tool=tool,
                reason="mcp_tool_missing_required_role",
            )

    return requirements


# ── Append-only dispatch audit (dwo-mcp-02-d5) ─────────────────────────────

#: Append-only table every attempt lands in. Created by migration 307 and by
#: ``tools/studio/init_db.py``; registered in APPEND_ONLY_TABLES in
#: ``.claude/hooks/pre_tool_use.py``.
AUDIT_TABLE = "studio_mcp_dispatch_audit"

#: The handler ran.
DECISION_ALLOWED = "allowed"

#: A gate, an unknown tool, bad params, or a raising handler stopped the call.
DECISION_REFUSED = "refused"

#: A ``requires_approval`` tool parked on a human gate nobody has decided.
DECISION_PENDING_APPROVAL = "pending_approval"

#: The closed decision vocabulary. The ``decision`` CHECK constraint in
#: migration 307 and ``init_db.py`` mirrors this tuple; the audit tests assert
#: the two have not drifted.
DECISIONS = (DECISION_ALLOWED, DECISION_REFUSED, DECISION_PENDING_APPROVAL)

#: Gate refusal reasons that mean "parked, not denied" — the attempt is still
#: live and a human decision will settle it, so it is audited as pending rather
#: than as a refusal.
_PENDING_REASONS = frozenset({"mcp_tool_awaiting_human_approval"})

#: Reason recorded for a dispatch that completed.
REASON_DISPATCHED = "dispatched"


def params_digest(params) -> str:
    """Return the SHA-256 digest of ``params``, canonicalised.

    Sorted keys and separator-normalised JSON, so two dispatches with the same
    arguments in a different order digest the same — otherwise "did the approver
    see these arguments" could not be answered by comparing digests. Values JSON
    cannot encode fall back to their repr rather than raising: an audit row with
    a weaker digest beats no audit row.
    """
    try:
        canonical = json.dumps(params or {}, sort_keys=True, separators=(",", ":"),
                               default=repr)
    except (TypeError, ValueError):
        canonical = repr(params)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def audit_classification(caller_il: str = "") -> str:
    """Marking for an audit row, derived from the caller's impact level.

    Delegates to ``classification_manager.get_classification_for_il`` rather
    than stamping a banner here: an IL6 dispatch is SECRET and must be marked
    SECRET. Falls back to the platform's IL4 marking when the level is unknown
    or the manager is unimportable — never to a hardcoded literal.
    """
    from tools.compliance.classification_manager import (  # noqa: PLC0415
        get_classification_for_il,
    )

    for level in ((caller_il or "").strip().upper(), DEFAULT_CALLER_IL):
        if not level:
            continue
        try:
            marking = get_classification_for_il(level)
        except Exception:  # noqa: BLE001 — an unknown level falls through to the baseline
            continue
        if marking:
            return str(marking)
    return get_classification_for_il(DEFAULT_CALLER_IL)


def record_dispatch_audit(
    tool: str,
    params,
    decision: str,
    reason: str,
    *,
    run_id: str = "",
    step_id: str = "",
    caller: dict | None = None,
    detail: str = "",
) -> tuple[bool, str]:
    """Append one row describing this attempt. Returns ``(written, why_not)``.

    Best-effort by design: the caller's dispatch has already been decided by the
    gates, and an unreachable audit store must not overturn that decision in
    either direction. The failure reason is returned so the step payload can say
    the audit did not land instead of implying it did.
    """
    import uuid  # noqa: PLC0415

    if decision not in DECISIONS:
        raise ValueError(
            f"Unknown audit decision {decision!r}; expected one of {', '.join(DECISIONS)}"
        )

    caller = caller or {}
    try:
        conn = _gate_connection()
        try:
            conn.execute(
                f"""INSERT INTO {AUDIT_TABLE}
                    (audit_id, run_id, step_id, tool, params_sha256,
                     principal_id, tenant_id, caller_il, caller_roles,
                     caller_source, decision, reason, detail, classification,
                     recorded_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s)""",
                (
                    f"aud-{uuid.uuid4().hex[:16]}",
                    run_id,
                    step_id,
                    tool,
                    params_digest(params),
                    str(caller.get("principal_id") or ""),
                    str(caller.get("tenant_id") or ""),
                    str(caller.get("impact_level") or ""),
                    ",".join(caller.get("roles") or ()),
                    str(caller.get("source") or ""),
                    decision,
                    reason,
                    detail[:2000],
                    audit_classification(str(caller.get("impact_level") or "")),
                    _utcnow(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return True, ""
    except Exception as exc:  # noqa: BLE001 — see docstring
        return False, f"{type(exc).__name__}: {exc}"


def query_dispatch_audit(
    run_id: str = "", tool: str = "", decision: str = "", limit: int = 200
) -> list[dict]:
    """Read audit rows back, newest first. Filters are ANDed; empty means "any"."""
    where, params = [], []
    for column, value in (("run_id", run_id), ("tool", tool), ("decision", decision)):
        if value:
            where.append(f"{column} = %s")
            params.append(value)
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    conn = _gate_connection()
    try:
        rows = conn.execute(
            f"SELECT * FROM {AUDIT_TABLE}{clause} ORDER BY recorded_at DESC, "
            f"audit_id DESC LIMIT %s",
            (*params, int(limit)),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _audit_outcome(exc: BaseException) -> tuple[str, str]:
    """Map a failed attempt onto ``(decision, reason)``.

    The reasons are the same strings the CLI reports as ``error_type``, so an
    audit row and the step's stdout name the same cause and neither has to be
    re-parsed to correlate with the other.
    """
    if isinstance(exc, MCPWorkflowGateError):
        reason = exc.reason or "mcp_tool_not_allowlisted"
        if reason in _PENDING_REASONS:
            return DECISION_PENDING_APPROVAL, reason
        return DECISION_REFUSED, reason
    if isinstance(exc, LookupError):
        return DECISION_REFUSED, "unknown_tool"
    if isinstance(exc, ValueError):
        return DECISION_REFUSED, "invalid_params"
    return DECISION_REFUSED, "dispatch_error"


# ── Run memory (dwo-mem-01) ────────────────────────────────────────────────

def write_run_memory(run_id: str, step_id: str, value: dict) -> tuple[bool, str]:
    """Persist a step result to run-scoped memory under ``step:<step_id>``.

    Soft dependency: run_memory is delivered by dwo-mem-01. Until it lands this
    is a no-op that reports why, rather than a second state store.
    """
    if not run_id:
        return False, "no --run-id supplied"
    try:
        from tools.studio import run_memory  # noqa: PLC0415
    except ImportError:
        return False, "tools.studio.run_memory not available (dwo-mem-01)"
    try:
        run_memory.set(run_id, f"{MEMORY_KEY_PREFIX}{step_id}", value)
        return True, ""
    except Exception as exc:  # noqa: BLE001 — memory must never fail the step
        return False, str(exc)


# ── Dispatch ───────────────────────────────────────────────────────────────

def _jsonable(value):
    """Coerce a handler return value into something json.dumps can emit."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return {"repr": repr(value)[:4000]}


def run(
    tool: str,
    params: dict,
    run_id: str = "",
    step_id: str = "",
    caller: dict | None = None,
    approval_wait: float | None = None,
) -> dict:
    """Authorize, look up, validate, gate, and dispatch a registry tool.

    Args:
        caller: Principal to dispatch as. Resolved from the run's workflow
            context when omitted (see :func:`resolve_caller`).
        approval_wait: Seconds to wait on a human gate, overriding
            :func:`approval_wait_seconds`. Only consulted for a
            ``requires_approval`` tool.

    Returns the step result payload.

    Every path through this function — dispatch, refusal, and a gate parked
    awaiting a human — appends exactly one row to :data:`AUDIT_TABLE` before
    returning or re-raising (dwo-mcp-02-d5). The audit write never changes the
    outcome; ``audit_written`` / ``audit_skipped`` in the payload report whether
    it landed.

    Raises:
        MCPWorkflowGateError: ``tool`` is not on the workflow allowlist, the
            caller does not clear its IL / role limits, or the tool's human gate
            was rejected or left undecided. The allowlist is checked first, so a
            refused tool is never resolved, imported, or called; IL and RBAC are
            checked after lookup (they read the tool's owning component from its
            module) but before params and dispatch, so a refused caller never
            reaches the handler either.
    """
    # Resolved before the allowlist so a refusal is audited with an actor rather
    # than anonymously. Touches run memory and the environment only — the tool's
    # registry entry is still not read until it has cleared the gate.
    if caller is None:
        try:
            caller = resolve_caller(run_id)
        except Exception:  # noqa: BLE001 — identity is for the record, not the decision
            caller = {}
    audit_step_id = step_id or f"mcp-{tool}"

    try:
        disposition = check_tool_allowed(tool)

        entry = resolve_entry(tool)

        requirements = check_caller_authorized(tool, caller, entry=entry)

        violations = validate_params(params, entry.get("input_schema") or {})
        if violations:
            raise ValueError(
                f"Invalid params for '{tool}' — "
                + "; ".join(violations[:10])
                + (f" (+{len(violations) - 10} more)" if len(violations) > 10 else "")
            )

        # Last check before dispatch, deliberately: a person should not be woken
        # to approve a call that the allowlist, the caller's IL, or its own
        # parameters would have refused anyway.
        approval: dict = {}
        if disposition == DISPOSITION_REQUIRES_APPROVAL:
            approval = await_approval(tool, run_id, wait_seconds=approval_wait)

        module_path, handler_name = entry["module"], entry["handler"]
        try:
            mod = importlib.import_module(module_path)
            handler = getattr(mod, handler_name)
        except (ImportError, AttributeError) as exc:
            raise RuntimeError(
                f"Cannot load handler {module_path}.{handler_name} for '{tool}': {exc}"
            ) from exc

        start = time.monotonic()
        result = handler(params)
        duration_ms = int((time.monotonic() - start) * 1000)
    except BaseException as exc:
        decision, reason = _audit_outcome(exc)
        record_dispatch_audit(
            tool, params, decision, reason,
            run_id=run_id, step_id=audit_step_id, caller=caller, detail=str(exc),
        )
        raise

    step_id = audit_step_id
    payload = {
        "tool": tool,
        "category": entry.get("category", ""),
        "handler": f"{module_path}.{handler_name}",
        "duration_ms": duration_ms,
        # What the dispatch was authorized under — the record d5 audits.
        "caller_il": caller.get("impact_level", ""),
        "required_il": requirements["min_il"],
        "component": requirements["component"],
        "result": _jsonable(result),
    }
    if approval:
        # Which gate authorized this dispatch — audited alongside it below.
        payload["approval"] = approval

    audited, audit_error = record_dispatch_audit(
        tool, params, DECISION_ALLOWED, REASON_DISPATCHED,
        run_id=run_id, step_id=step_id, caller=caller,
        detail=f"{module_path}.{handler_name} in {duration_ms}ms",
    )

    written, reason = write_run_memory(run_id, step_id, payload)
    payload["step_id"] = step_id
    payload["memory_key"] = f"{MEMORY_KEY_PREFIX}{step_id}"
    payload["memory_written"] = written
    if not written:
        payload["memory_skipped"] = reason
    payload["audit_written"] = audited
    if not audited:
        payload["audit_skipped"] = audit_error
    return payload


def main():
    parser = argparse.ArgumentParser(description="MCP Tool Executor (shared)")
    parser.add_argument("--tool", required=True, help="TOOL_REGISTRY tool name")
    parser.add_argument("--params", default="{}", help="Tool arguments as a JSON object")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--step-id", default="", help="Run-memory key suffix")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--json", action="store_true", help="Accepted for runner parity")
    parser.add_argument("--caller-il", default="",
                        help="Caller impact level (IL2|IL4|IL5|IL6); overrides run context")
    parser.add_argument("--caller-roles", default="",
                        help="Comma-separated caller roles; overrides run context")
    parser.add_argument("--caller-id", default="", help="Caller principal id")
    parser.add_argument("--tenant-id", default="", help="Caller tenant id")
    parser.add_argument("--approval-wait", default="",
                        help="Seconds to wait on a human gate (requires_approval "
                             "tools only); 0 parks the gate without blocking")
    args = parser.parse_args()

    try:
        params = parse_params(args.params)
        caller = resolve_caller(args.run_id, {
            "impact_level": args.caller_il,
            "roles": args.caller_roles,
            "principal_id": args.caller_id,
            "tenant_id": args.tenant_id,
        })
        wait = float(args.approval_wait) if str(args.approval_wait).strip() else None
        payload = run(args.tool, params, args.run_id, args.step_id, caller, wait)
        print(json.dumps({"status": "success", **payload}))
        sys.exit(0)
    except MCPWorkflowGateError as exc:
        # Before LookupError/Exception: this is a RuntimeError and must not be
        # reported as a generic dispatch failure — the step was refused, not run.
        refusal = {"status": "failed",
                   "error_type": exc.reason or "mcp_tool_not_allowlisted",
                   "tool": args.tool, "error": str(exc)}
        if exc.step_run_id:
            refusal["step_run_id"] = exc.step_run_id
        print(json.dumps(refusal))
        sys.exit(1)
    except LookupError as exc:
        print(json.dumps({"status": "failed", "error_type": "unknown_tool",
                          "tool": args.tool, "error": str(exc)}))
        sys.exit(1)
    except ValueError as exc:
        print(json.dumps({"status": "failed", "error_type": "invalid_params",
                          "tool": args.tool, "error": str(exc)}))
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "failed", "error_type": "dispatch_error",
                          "tool": args.tool,
                          "error": f"{type(exc).__name__}: {exc}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
