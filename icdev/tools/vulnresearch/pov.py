# CUI // SP-CTI
"""Proofs of vulnerability, replayed in the sandbox through reproduction_validator (icx-ent-01).

A candidate becomes a finding ONLY here, and only when its PoV discriminates:

* the PoV is built from the declared recipe for the candidate's weakness class (``recipes`` in
  ``args/vuln_research.yaml``), or is the crashing input a fuzz harness found. Recipes are canary-only: a payload
  can create an empty file under /tmp, or read a canary planted for it, inside the sandbox, and nothing else. Nothing
  here generates a variant of a PoV, and no model writes one;
* it is a ``sandbox_pov`` :class:`~tools.security.reproduction_validator.Reproduction`, replayed through
  :func:`~tools.security.reproduction_validator.replay` by the engine this module registers
  (:func:`register`). That engine scope-locks the target first (``targets.assert_in_scope``) and runs
  ``sandbox_runtime/pov_runner.py`` in :meth:`SandboxExecutor.run_isolated` (no network, read-only root, the
  target mounted read-only);
* discrimination is the validator's own decision table (``decide_discrimination``) with ``POV_VOCABULARY``: it must
  fire on the PoV input and NOT on the recipe's benign control. A PoV that fires on both is a tautology and the
  candidate is dropped;
* a PoV is SEALED: its inputs are kept in the ledger and used only to replay; every view and the exported bundle
  carry its digest, oracle and replay outcomes, never the payload.

The executor is :data:`EXECUTOR` (a test may swap in a double); the product never runs target code any other way.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

from tools.security import reproduction_validator as rv
from tools.security.redteam_scope import ScopeViolation
from tools.vulnresearch import ledger

RUNTIME = Path(__file__).resolve().parent / "sandbox_runtime"

#: ``() -> executor`` with ``run_isolated``. Swapped by tests for a double that runs the same runner scripts.
EXECUTOR: Callable[[], Any] = lambda: __import__(  # noqa: E731
    "tools.security.sandbox_executor", fromlist=["SandboxExecutor"]).SandboxExecutor()


def token_for(key: str) -> str:
    return ledger.digest(["vr-canary", key])[:16]


def _canary_pickle(path: str) -> str:
    # protocol 0: builtins.open(path, "w") -- creates an empty canary file when unpickled, nothing else.
    raw = f"cbuiltins\nopen\n(V{path}\nVw\ntR.".encode("latin-1")
    return base64.b64encode(raw).decode()


def _benign_pickle() -> str:
    import pickle  # nosec B403 — builds a benign pickle of a dict for the control input

    return base64.b64encode(pickle.dumps({"theme": "dark"}, protocol=0)).decode()


def materialise(recipe: dict, key: str, *, crash_input: Optional[str] = None,
                seed_input: Optional[str] = None) -> dict:
    """The PoV and control inputs for one candidate, from its recipe. Deterministic given ``key``."""
    token = token_for(key)
    canary = f"/tmp/vr_canary_{token}"
    fields = {"canary": canary, "token": token, "dotdots": "../" * 24, "canary_rel": canary.lstrip("/")}

    def one(value: str) -> dict:
        if value == "@canary_pickle":
            return {"input": _canary_pickle(canary), "input_encoding": "text"}
        if value == "@benign_pickle":
            return {"input": _benign_pickle(), "input_encoding": "text"}
        if value == "@fuzz_crash":
            return {"input": crash_input or "", "input_encoding": "base64"}
        if value == "@fuzz_seed":
            return {"input": base64.b64encode((seed_input or "").encode("latin-1", "replace")).decode(),
                    "input_encoding": "base64"}
        return {"input": str(value).format(**fields), "input_encoding": "text"}

    return {"token": token, "canary": canary, "oracle": recipe["oracle"], "plant": bool(recipe.get("plant")),
            "pov": one(str(recipe["payload"])), "control": one(str(recipe["control"]))}


def sealed_digest(sealed: dict) -> str:
    return ledger.digest(sealed)


# ── the sandbox replay engine registered into reproduction_validator ───────────


def _workspace() -> Path:
    base = os.environ.get("ICDEV_VR_DATA_DIR") or ""
    root = Path(base) / "work" if base else None
    if root:
        root.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix="vr-", dir=str(root) if root else None))
    os.chmod(d, 0o755)
    return d


def _readable(path: Path) -> None:
    """The container runs as nobody: the read-only mounts must be world-readable."""
    for p in [path, *path.rglob("*")]:
        mode = p.stat().st_mode
        p.chmod(mode | stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH | (0o111 if p.is_dir() else 0))


def _sandbox_cfg() -> dict:
    from tools.vulnresearch.targets import load_config

    return load_config().get("sandbox") or {}


def isolation_decision(scoped: dict, changed: Optional[dict] = None) -> dict:
    """May this target's code run here? ``{allowed, isolation, waiver, why}``.

    Only under gVisor or a microVM (``isolation.require``), or a plain container under the one named waiver whose
    scope the target is: the synthetic benchmark at its pinned tree. A patched copy of the benchmark (``changed``:
    ``{path: sha256}`` of every file the patch changed) stays under the waiver only when every changed file is one
    of the benchmark's committed reference fixes (``benchmark.reference_fixes``); any other patch is code that is
    not ours. The waiver is returned so the run and the bundle carry it.
    """
    from tools.security.sandbox_executor import isolated_profile
    from tools.vulnresearch import targets

    cfg = targets.load_config()
    iso = cfg.get("isolation") or {}
    prof = isolated_profile()
    if prof["isolation"] in (iso.get("require") or ["gvisor", "microvm"]):
        return {"allowed": True, "isolation": prof["isolation"], "waiver": None, "why": ""}
    if targets.is_synthetic_benchmark(scoped, cfg) and changed:
        bad = targets.not_reference_fixes(changed, cfg)
        if bad:
            return {"allowed": False, "isolation": prof["isolation"], "waiver": None,
                    "why": (f"refused: the patch changes {', '.join(bad)} to content that is not one of the "
                            "benchmark's committed reference fixes, so it is code that is not ours and runs only "
                            f"under gVisor or a microVM; the engine runs {prof['oci_runtime']} (a plain container)")}
    if targets.is_synthetic_benchmark(scoped, cfg):
        for w in iso.get("waivers") or []:
            if w.get("applies_to") == "synthetic_benchmark" and w.get("id") and w.get("reason"):
                return {"allowed": True, "isolation": prof["isolation"], "why": "",
                        "waiver": {k: w.get(k) for k in ("id", "applies_to", "reason", "recorded_by",
                                                          "recorded_on")}}
    return {"allowed": False, "isolation": prof["isolation"], "waiver": None,
            "why": (f"refused: {scoped['target_id']} is not the synthetic benchmark, so its code runs only under "
                    f"gVisor or a microVM, and the engine runs {prof['oci_runtime']} (a plain container). An "
                    "operator installs one and sets sandbox.isolated.oci_runtime (an owner/root step).")}


def stage(root: Path, files: list[dict], dest: Path) -> Path:
    """Copy EXACTLY the files the tree digest hashed into ``dest``: regular files only, each re-hashed on the way.

    The sandbox mounts this, never the target's root, so a shared object, bytecode, a .pth file or a symlink that
    sits beside the code (and that the pin cannot see) never reaches it. A file that is not a regular file now, or
    whose bytes no longer hash to what was recorded, is refused (it changed after the scope lock).
    """
    import hashlib

    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        rel = Path(f["path"])
        if rel.is_absolute() or ".." in rel.parts:
            raise ScopeViolation(f"refused to stage {f['path']!r}")
        src = Path(root) / rel
        st = os.lstat(src)
        if not stat.S_ISREG(st.st_mode):
            raise ScopeViolation(f"{f['path']} is not a regular file any more")
        data = src.read_bytes()
        if hashlib.sha256(data).hexdigest() != f["sha256"]:
            raise ScopeViolation(f"{f['path']} changed after the scope lock")
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        out.chmod(0o644)
    _readable(dest)
    return dest


def run_in_sandbox(script: str, spec: dict, *, scoped: dict, copy: Optional[dict] = None,
                   image: Optional[str] = None, extra_files: Optional[dict] = None) -> dict:
    """Run one runner script on one spec in the isolated sandbox. ``{status, result, stderr, profile, waiver}``.

    ``scoped`` is the target's ``assert_in_scope`` result: its isolation is decided here, every time, and a run
    that is not allowed is refused before anything is staged. What is mounted at /target is always a STAGED copy
    of exactly the hashed files (:func:`stage`), or a patched copy this module built the same way (``copy``).
    """
    from tools.security.sandbox_executor import isolated_profile

    gate = isolation_decision(scoped, changed=(copy or {}).get("changed"))
    if not gate["allowed"]:
        return {"status": "refused", "result": None, "stderr": "", "exit_code": -1, "refusal": gate["why"],
                "profile": isolated_profile(), "waiver": None}
    cfg = _sandbox_cfg()
    work = _workspace()
    harness, out = work / "harness", work / "out"
    harness.mkdir()
    out.mkdir()
    os.chmod(out, 0o777)  # nosec B103 — a throwaway dir the unprivileged container user writes its result to
    try:
        try:
            target_root = copy["root"] if copy else stage(scoped["root"], scoped["tree"]["files"], work / "target")
        except ScopeViolation as exc:
            return {"status": "refused", "result": None, "stderr": "", "exit_code": -1, "refusal": str(exc),
                    "profile": isolated_profile(), "waiver": None}
        for name in ("pov_runner.py", "fuzz_runner.py"):
            shutil.copyfile(RUNTIME / name, harness / name)
        for name, text in (extra_files or {}).items():
            (harness / name).write_text(text, encoding="utf-8")
        (harness / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        _readable(harness)
        ex = EXECUTOR()
        res = ex.run_isolated(["python", f"/harness/{script}", "/harness/spec.json"],
                              image=image or cfg.get("image") or "python:3.12-slim",
                              mounts=[(str(target_root), "/target", "ro"), (str(harness), "/harness", "ro"),
                                      (str(out), "/out", "rw")],
                              timeout_seconds=int(cfg.get("timeout_seconds") or 60),
                              max_memory_mb=int(cfg.get("memory_mb") or 512),
                              pids_limit=int(cfg.get("pids_limit") or 128),
                              requires_strong_isolation=True, waiver=gate["waiver"],
                              waiver_tree_digest=(scoped.get("tree") or {}).get("digest") or "")
        if res.status == "refused":
            return {"status": "refused", "result": None, "stderr": "", "exit_code": -1, "refusal": res.error,
                    "profile": isolated_profile(), "waiver": None}
        result = None
        if res.status == "completed":
            if script == "pov_runner.py":
                lines = [ln for ln in (res.stdout or "").splitlines() if ln.strip().startswith("{")]
                result = json.loads(lines[-1]) if lines else None
            else:
                f = out / f"{spec['id']}.json"
                result = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
        return {"status": res.status, "result": result, "stderr": (res.stderr or res.error or "")[-800:],
                "exit_code": res.exit_code, "profile": isolated_profile(), "waiver": gate["waiver"]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


#: Proofs in flight: an opaque token -> what this module sealed for one candidate of one target. A discovery run
#: proves its candidates BEFORE it records them, so they cannot yet be read back by finding id; the token is
#: minted here, lives only for the two replays, and nothing outside this module can name what it points at.
_PENDING: dict[str, dict] = {}
#: Patched copies this module made (token -> {target_id, root}), only under its own workspace.
_COPIES: dict[str, dict] = {}
#: The only keys a sandbox_pov step may carry. A spec, a root, a path or an input is never taken from a request.
_STEP_KEYS = {"proof", "finding_id", "input", "copy"}


def _refused(why: str) -> rv.ReplayResult:
    return rv.ReplayResult(outcome=rv.ReplayOutcome.refused, detail=why)


def make_copy(target_id: str, scoped: dict) -> tuple[str, Path]:
    """A throwaway STAGED copy of a scope-locked tree (exactly its hashed files, :func:`stage`) under this module's
    workspace, named by an opaque token. Nothing is copied by walking the root, so no symlink is ever followed."""
    import secrets

    work = _workspace()
    dst = stage(scoped["root"], scoped["tree"]["files"], work / "tree")
    token = secrets.token_hex(16)
    _COPIES[token] = {"target_id": target_id, "root": dst.resolve(), "work": work.resolve(),
                      "base": {f["path"]: f["sha256"] for f in scoped["tree"]["files"]}, "changed": None}
    return token, dst


def note_patched(token: str) -> dict:
    """After a patch is applied to a copy: ``{path: sha256}`` of every file it changed. The copy must still hold
    exactly the base's files (a patch only modifies), else it is refused."""
    from tools.vulnresearch import targets

    info = _COPIES[token]
    got = targets.tree(info["root"], targets.load_config())
    have = {f["path"]: f["sha256"] for f in got["files"]}
    if set(have) != set(info["base"]):
        raise ScopeViolation("the patched copy does not hold exactly the target's files")
    info["changed"] = {p: s for p, s in sorted(have.items()) if info["base"][p] != s}
    return info["changed"]


def copy_info(token: str) -> Optional[dict]:
    return _COPIES.get(token)


def drop_copy(token: str) -> None:
    info = _COPIES.pop(token, None)
    if info:
        shutil.rmtree(info["work"], ignore_errors=True)


def _sealed_for_step(repro: rv.Reproduction, step: dict):
    """``(candidate, sealed)`` this module holds for the step, or a refusal reason."""
    if step.get("proof"):
        held = _PENDING.get(str(step["proof"]))
        if not held or held["target_id"] != repro.target:
            return None, "no proof in flight with that token for this target"
        return held, None
    if step.get("finding_id"):
        from tools.vulnresearch import discovery

        got = discovery.sealed_for(str(step["finding_id"]))
        if got is None or got[0]["target_id"] != repro.target:
            return None, "no recorded finding with that id on this target"
        finding, sealed = got
        return {"target_id": finding["target_id"], "module": finding["module"], "function": finding["function"],
                "src_root": None, "sealed": sealed}, None
    return None, "a sandbox_pov step names a proof token or a finding id"


def _replayer(repro: rv.Reproduction) -> rv.ReplayResult:
    """The ``sandbox_pov`` engine. It trusts nothing a request carries but WHICH sealed proof to replay:

    * the step may name only a proof in flight (an opaque token this module minted) or a recorded finding id, the
      input (``pov`` or ``control``) and optionally a patched copy this module made; any other key is refused;
    * the input, oracle, module and function come from the SEALED PoV, never from the request;
    * the tree is the scope-locked target (``targets.assert_in_scope``) or a copy this module made of it;
    * the sandbox's stderr is never handed back (it could carry host or target text).
    """
    from tools.vulnresearch import targets

    if len(repro.steps) != 1 or not isinstance(repro.steps[0], dict):
        return _refused("a sandbox_pov reproduction has exactly one step")
    step = repro.steps[0]
    extra = set(step) - _STEP_KEYS
    if extra:
        return _refused(f"a sandbox_pov step may not carry {sorted(extra)}: only the sealed PoV is replayed")
    which = str(step.get("input") or "")
    if which not in ("pov", "control"):
        return _refused("input must be pov or control")
    held, why = _sealed_for_step(repro, step)
    if held is None:
        return _refused(why)
    try:
        scoped = targets.assert_in_scope(repro.target)
    except ScopeViolation as exc:
        return _refused(str(exc))
    info = None
    if step.get("copy"):
        info = _COPIES.get(str(step["copy"]))
        if not info or info["target_id"] != repro.target:
            return _refused("no patched copy with that token for this target")
    cfg = targets.load_config()
    src_root = held.get("src_root") or scoped["registration"].get("src_root") or "."
    spec = _spec(held, held["sealed"], which, cfg, src_root)
    run = run_in_sandbox("pov_runner.py", spec, scoped=scoped, copy=info)
    if run["status"] != "completed" or not run["result"]:
        outcome = {"unavailable": rv.ReplayOutcome.unavailable, "refused": rv.ReplayOutcome.refused}.get(
            run["status"], rv.ReplayOutcome.error)
        return rv.ReplayResult(outcome=outcome, detail=f"sandbox {run['status']}" + (
            f": {run.get('refusal')}" if run.get("refusal") else ""))
    r = run["result"]
    if not r.get("decisive"):
        return rv.ReplayResult(outcome=rv.ReplayOutcome.error, detail="the replay could not decide",
                               observations=[{"oracle": r.get("oracle"), "exception": r.get("exception")}])
    return rv.ReplayResult(outcome=rv.ReplayOutcome.reproduced if r["fired"] else rv.ReplayOutcome.not_reproduced,
                           observations=[{"oracle": r.get("oracle"), "fired": r["fired"],
                                          "exception": r.get("exception")}])


def register() -> None:
    rv.register_pov_replayer(_replayer)


def _repro(target_id: str, oracle: str, step: dict) -> rv.Reproduction:
    return rv.Reproduction(kind="sandbox_pov", target=target_id, steps=[step],
                           predicate={"type": "pov_oracle", "oracle": oracle})


def _spec(c: dict, sealed: dict, which: str, cfg: dict, src_root: str) -> dict:
    inp = sealed[which]
    return {"which": which, "module": c["module"], "function": c["function"], "src_root": src_root,
            "input": inp["input"], "input_encoding": inp["input_encoding"], "oracle": sealed["oracle"],
            "canary": sealed["canary"], "token": sealed["token"], "plant": sealed["plant"],
            "expected_exceptions": (cfg.get("fuzz") or {}).get("expected_exceptions") or ["ValueError"],
            "call_timeout": int((cfg.get("sandbox") or {}).get("call_timeout_seconds") or 5)}


def replay_pair(target_id: str, c: dict, sealed: dict, cfg: dict, src_root: str, *,
                against: str = "control", copy_token: Optional[str] = None,
                finding_id: Optional[str] = None) -> dict:
    """Replay the PoV, then either its benign control (``against='control'``: is this a finding?) or the same PoV
    on a patched copy this module made (``against='patched'``: does the patch fix it?). The validator decides.

    A candidate not yet recorded is replayed by an opaque proof token held only for these two replays; a recorded
    finding by its id. Either way the request names WHICH sealed proof, never its content.
    """
    import secrets

    register()
    token = None
    if finding_id:
        base = {"finding_id": finding_id}
    else:
        token = secrets.token_hex(16)
        _PENDING[token] = {"target_id": target_id, "module": c["module"], "function": c["function"],
                           "src_root": src_root, "sealed": sealed}
        base = {"proof": token}
    try:
        first = rv.replay(_repro(target_id, sealed["oracle"], {**base, "input": "pov"}))
        if against == "control":
            second = rv.replay(_repro(target_id, sealed["oracle"], {**base, "input": "control"}))
            vocab = rv.POV_VOCABULARY
        else:
            second = rv.replay(_repro(target_id, sealed["oracle"], {**base, "input": "pov", "copy": copy_token}))
            vocab = rv.PATCH_VOCABULARY
    finally:
        if token:
            _PENDING.pop(token, None)
    ok, reason = rv.decide_discrimination(
        before_fired=first.reproduced, before_decisive=first.decisive, before_outcome=first.outcome,
        after_fired=second.reproduced, after_decisive=second.decisive, after_outcome=second.outcome,
        vocabulary=vocab)
    return {"discriminating": ok, "reason": reason, "first": first.to_dict(), "second": second.to_dict(),
            "against": against}


def drop_reason(proof: dict) -> Optional[str]:
    """None for a finding; else why the candidate is dropped."""
    if proof["discriminating"]:
        return None
    a, b = proof["first"]["outcome"], proof["second"]["outcome"]
    if rv.ReplayOutcome.unavailable in (a, b) or rv.ReplayOutcome.refused in (a, b):
        return "sandbox_unavailable"
    if a not in (rv.ReplayOutcome.reproduced, rv.ReplayOutcome.not_reproduced) or \
            b not in (rv.ReplayOutcome.reproduced, rv.ReplayOutcome.not_reproduced):
        return "indecisive"
    if a == rv.ReplayOutcome.reproduced and b == rv.ReplayOutcome.reproduced:
        return "tautology"
    return "not_reproduced"
