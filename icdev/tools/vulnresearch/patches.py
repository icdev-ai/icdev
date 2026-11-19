# CUI // SP-CTI
"""Patch proposals: a patch whose PoV now fails and whose tests pass, reviewed by a person (icx-ent-01).

A patch is only ever a PROPOSAL, AIxCC-style: it counts only when it is backed by the finding's own proof of
vulnerability.

* **Propose** (anyone, or an agent's draft): a unified diff for one confirmed finding. Every path it touches must be
  an in-scope file of the target. It is applied with ``git apply`` to a THROWAWAY COPY of the target (never the
  target), then, in the sandbox with no network: the finding's sealed PoV is replayed against the vulnerable tree and
  the patched copy, and must fire on the first and FAIL on the second (reproduction_validator's decision table,
  ``PATCH_VOCABULARY``); and the target's tests (the registration's ``test_dir``, ``python -m unittest``) must pass
  on the patched copy. A diff that does not do both is refused and recorded nowhere. One that does is recorded, at
  its digest, as ``patch_proposed`` on ``patch:<finding id>``.
* **Draft** (:func:`draft`): a model drafts the diff through Cortex governance and the LLM router
  (``llm_function: vuln_patch_draft``); the draft is then proposed as the agent's, exactly as above.
* **Review** (``decl-wft-vr-patch-review``): a person who did not propose it accepts or rejects it, with a reason,
  bound to the proposal's digest. Nothing is ever applied to the target by this feature.
"""
from __future__ import annotations

import re
import subprocess  # nosec B404 — git apply on a throwaway copy, fixed arguments
from pathlib import Path
from typing import Optional

from tools.security.redteam_scope import ScopeViolation
from tools.vulnresearch import discovery, ledger, pov, targets
from tools.vulnresearch.constants import GENESIS, SURFACE_PATCH, TEMPLATE_PATCH

PatchRefused = ledger.LedgerRefused

_PATH_RE = re.compile(r"^(?:\+\+\+|---) (?:[ab]/)?(\S+)", re.M)


def diff_paths(diff: str) -> list[str]:
    paths = sorted({p for p in _PATH_RE.findall(diff or "") if p != "/dev/null"})
    for p in paths:
        if p.startswith("/") or ".." in Path(p).parts:
            raise PatchRefused(400, "diff_path", f"the diff touches {p!r}: only paths inside the target")
    return paths


def _apply(copy: Path, diff: str) -> Optional[str]:
    patch = copy.parent / "change.diff"
    patch.write_text(diff if diff.endswith("\n") else diff + "\n", encoding="utf-8")
    for args in (["git", "apply", "--check", str(patch)], ["git", "apply", str(patch)]):
        r = subprocess.run(args, cwd=str(copy), capture_output=True, text=True, timeout=30)  # nosec B603 B607
        if r.returncode != 0:
            return (r.stderr or r.stdout or "git apply failed").strip()[:400]
    pov._readable(copy)
    return None


def run_tests(root: Path, test_dir: str, scoped: Optional[dict] = None) -> dict:
    """The target's unittest suite in the isolated sandbox. ``{status, passed, detail}``."""
    cfg = targets.load_config().get("sandbox") or {}
    ex = pov.EXECUTOR()
    res = ex.run_isolated(["python", "-m", "unittest", "discover", "-s", f"/target/{test_dir}"],
                          image=cfg.get("image") or "python:3.12-slim", mounts=[(str(root), "/target", "ro")],
                          timeout_seconds=int(cfg.get("timeout_seconds") or 60),
                          max_memory_mb=int(cfg.get("memory_mb") or 512), pids_limit=int(cfg.get("pids_limit") or 128))
    tail = ((res.stderr or "") + (res.error or ""))[-400:]
    return {"status": res.status, "passed": res.status == "completed" and res.exit_code == 0,
            "exit_code": res.exit_code, "detail": tail}


def _stream(fid: str) -> str:
    return f"patch:{fid}"


def replay_stream(ordered: list[dict]) -> dict:
    prop = None
    history = []
    for r in ordered:
        p = ledger.payload(r)
        h = {k: r[k] for k in ("event_id", "event", "actor", "actor_kind", "actor_role", "step_id", "reason",
                               "content_digest", "created_at", "row_hash")}
        history.append(h)
        if r["event"] == "patch_proposed":
            prop = {**h, "proposal": p.get("content") or {}, "decided": None}
        elif prop and (p.get("detail") or {}).get("proposed_event") == prop["event_id"]:
            prop["decided"] = h
    return {"proposed": prop, "history": history}


def state(fid: str, conn=None) -> dict:
    own = conn is None
    conn = conn or ledger.connection()
    try:
        ordered, problems = ledger.stream_rows(_stream(fid), conn)
    finally:
        if own:
            conn.close()
    st = replay_stream(ordered)
    st["chain"] = {"intact": not problems, "problems": problems}
    st["builders"] = {st["proposed"]["actor"]: "proposed this patch"} if st["proposed"] else {}
    st["_head"] = ledger.head(ordered)
    return st


def validate(fid: str, diff: str) -> dict:
    """Apply ``diff`` to a copy and prove it in the sandbox. Raises :class:`PatchRefused` with the validation."""
    got = discovery.sealed_for(fid)
    if got is None:
        raise PatchRefused(404, "unknown_finding", f"no confirmed finding {fid!r}")
    finding, sealed = got
    try:
        scoped = targets.assert_in_scope(finding["target_id"])
    except ScopeViolation as exc:
        raise PatchRefused(403, "out_of_scope", str(exc)) from exc
    paths = diff_paths(diff)
    if not paths:
        raise PatchRefused(400, "diff", "the diff names no file (use --- a/<path> / +++ b/<path>)")
    for p in paths:
        try:
            targets.in_scope_path(scoped, p)
        except ScopeViolation as exc:
            raise PatchRefused(403, "diff_path", str(exc)) from exc
    reg = scoped["registration"]
    if not reg.get("test_dir"):
        raise PatchRefused(422, "no_tests", "the target declares no tests: a patch cannot be shown to keep them passing")
    copy_token, copy = pov.make_copy(finding["target_id"], scoped["root"])
    try:
        err = _apply(copy, diff)
        if err:
            raise PatchRefused(422, "does_not_apply", f"the diff does not apply to the target: {err}")
        cfg = targets.load_config()
        proof = pov.replay_pair(finding["target_id"], {"module": finding["module"], "function": finding["function"]},
                                sealed, cfg, reg.get("src_root") or ".", against="patched", copy_token=copy_token,
                                finding_id=fid)
        tests = run_tests(copy, reg["test_dir"], scoped=scoped)
    finally:
        pov.drop_copy(copy_token)
    validation = {"pov_vulnerable": proof["first"]["outcome"], "pov_patched": proof["second"]["outcome"],
                  "pov_fails_after": proof["discriminating"], "reason": proof["reason"],
                  "tests_pass": tests["passed"], "tests": {k: tests[k] for k in ("status", "exit_code")},
                  "paths": paths}
    if not proof["discriminating"] or not tests["passed"]:
        why = proof["reason"] if not proof["discriminating"] else f"the target's tests do not pass: {tests['detail'][-200:]}"
        exc = PatchRefused(422, "not_validated", f"the patch is not proposed: {why}")
        exc.validation = validation  # type: ignore[attr-defined]
        raise exc
    return {"finding": finding, "validation": validation}


def propose(fid: str, diff: str, actor: Optional[dict], *, note: str = "", conn=None) -> dict:
    """Validate a patch in the sandbox and record it as a proposal. Decides nothing."""
    if not isinstance(actor, dict) or not actor.get("id"):
        raise PatchRefused(401, "no_actor", "proposing a patch needs a signed-in person or a named agent")
    v = validate(fid, diff)
    content = {"finding_id": fid, "target_id": v["finding"]["target_id"], "diff": diff,
               "diff_digest": ledger.digest(diff), "validation": v["validation"],
               "provenance": "modelled" if ledger.actor_kind(actor) == "agent" else "person"}
    own = conn is None
    conn = conn or ledger.connection()
    try:
        st = state(fid, conn)
        if st["proposed"] and not st["proposed"]["decided"]:
            raise PatchRefused(409, "pending", "a proposal for this finding is waiting for a person: review it first")
        row = ledger.append(conn, stream=_stream(fid), event="patch_proposed", subject=v["finding"]["target_id"],
                            actor=str(actor["id"]), actor_kind=ledger.actor_kind(actor),
                            actor_role=str(actor.get("role") or ""), content=content, reason=note,
                            prev_hash=st["_head"] or GENESIS)
    finally:
        if own:
            conn.close()
    return {"finding_id": fid, "event_id": row["event_id"], "content_digest": row["content_digest"],
            "validation": v["validation"], "proposed_by": row["actor"], "template": TEMPLATE_PATCH,
            "next": "a person who did not propose it accepts or rejects it on /vulnresearch/"}


def draft(fid: str, actor: Optional[dict], *, agent: str = "vulnresearch") -> dict:
    """A model drafts the diff (Cortex governance, the LLM router), proposed as the agent's. Raises when no model."""
    if discovery._no_model():
        raise PatchRefused(503, "no_model", "no model is configured (ICDEV_NO_LLM): propose a diff yourself")
    got = discovery.sealed_for(fid)
    if got is None:
        raise PatchRefused(404, "unknown_finding", f"no confirmed finding {fid!r}")
    finding, _ = got
    scoped = targets.assert_in_scope(finding["target_id"])
    cfg = targets.load_config()
    source = targets.in_scope_path(scoped, finding["file"]).read_text(encoding="utf-8")
    tmpl = (targets.BASE_DIR / "hardprompts" / "vulnresearch" / "patch_draft.md").read_text(encoding="utf-8")
    prompt = tmpl.format(cwe=finding["cwe"], name=finding.get("weakness") or "", file=finding["file"],
                         function=finding["function"], source=source)
    try:
        from tools.cortex import api
        from tools.cortex.schemas import CortexContext

        res = api.complete(prompt, function=str((cfg.get("patch") or {}).get("llm_function") or "vuln_patch_draft"),
                           ctx=CortexContext(user_id=str((actor or {}).get("id") or ""), classification="CUI",
                                             agent_id=agent))
    except Exception as exc:  # noqa: BLE001
        raise PatchRefused(503, "model_unavailable", f"the draft did not run: {type(exc).__name__}: {exc}") from exc
    text = str(res.text or "")
    m = re.search(r"(?ms)^--- .*", text)
    diff = m.group(0).split("```")[0] if m else ""
    return propose(fid, diff, {"id": f"agent:{agent}", "role": "service"},
                   note=f"model draft requested by {(actor or {}).get('id', 'unknown')}")


def review(fid: str, *, decision: str, reason: str, read_digest: str, actor: Optional[dict], conn=None) -> dict:
    """A person who did not propose the patch accepts or rejects it (declared step). Nothing is applied."""
    from tools.workflow_hitl import declared

    if not isinstance(actor, dict):
        raise PatchRefused(401, "no_person", "reviewing a patch is a signed-in person's declared step")
    own = conn is None
    conn = conn or ledger.connection()
    try:
        st = state(fid, conn)
        if not st["chain"]["intact"]:
            raise PatchRefused(409, "chain_broken", "the patch ledger does not verify")
        p = st["proposed"]
        if p is None:
            raise PatchRefused(409, "nothing_proposed", f"no patch for {fid} is proposed")
        if p["decided"]:
            raise PatchRefused(409, "already_decided", f"the proposal was already {p['decided']['event']}")
        if str(read_digest or "").strip() != p["content_digest"]:
            raise PatchRefused(409, "digest", "the proposal is not what you read (reload it)")
        dec = str(decision or "").strip().lower()
        try:
            taken = declared.take_step(TEMPLATE_PATCH, surface=SURFACE_PATCH, decision=dec, subject=f"vr_patch:{fid}",
                                       reason=str(reason or "").strip(), actor=actor, separated_from=st["builders"],
                                       payload={"finding_id": fid, "content_digest": p["content_digest"],
                                                "diff_digest": p["proposal"].get("diff_digest")})
        except declared.StepRefused as exc:
            raise PatchRefused(exc.status, exc.code, exc.message) from exc
        event = "patch_accepted" if taken["affirmative"] else "patch_rejected"
        row = ledger.append(conn, stream=_stream(fid), event=event, subject=p["proposal"].get("target_id", ""),
                            actor=taken["decided_by"], actor_kind="person", actor_role=str(actor.get("role") or ""),
                            step_id=taken["step_id"], reason=str(reason or "").strip(), prev_hash=st["_head"],
                            detail={"proposed_event": p["event_id"], "decision": dec})
    finally:
        if own:
            conn.close()
    return {"finding_id": fid, "event": event, "event_id": row["event_id"], "step": taken,
            "decided_by": taken["decided_by"], "proposed_by": p["actor"], "applied": False}


def patches_for(target_id: str, conn=None) -> dict:
    """``{finding id: state}`` for every patch stream of a target."""
    own = conn is None
    conn = conn or ledger.connection()
    try:
        ch = ledger.chain(ledger.read(conn, prefix="patch:"))
    finally:
        if own:
            conn.close()
    out = {}
    for stream, ordered in ch.items():
        if stream == "_problems" or not ordered or ordered[0]["subject"] != target_id:
            continue
        out[stream.split(":", 1)[1]] = replay_stream(ordered)
    return out
