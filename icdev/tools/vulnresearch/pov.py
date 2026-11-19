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


def run_in_sandbox(script: str, spec: dict, *, target_root: Path, image: Optional[str] = None,
                   extra_files: Optional[dict] = None) -> dict:
    """Run one runner script on one spec in the isolated sandbox. ``{status, result, stderr, profile}``."""
    from tools.security.sandbox_executor import isolated_profile

    cfg = _sandbox_cfg()
    work = _workspace()
    harness, out = work / "harness", work / "out"
    harness.mkdir()
    out.mkdir()
    os.chmod(out, 0o777)  # nosec B103 — a throwaway dir the unprivileged container user writes its result to
    try:
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
                              pids_limit=int(cfg.get("pids_limit") or 128))
        result = None
        if res.status == "completed":
            if script == "pov_runner.py":
                lines = [ln for ln in (res.stdout or "").splitlines() if ln.strip().startswith("{")]
                result = json.loads(lines[-1]) if lines else None
            else:
                f = out / f"{spec['id']}.json"
                result = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
        return {"status": res.status, "result": result, "stderr": (res.stderr or res.error or "")[-800:],
                "exit_code": res.exit_code, "profile": isolated_profile()}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _replayer(repro: rv.Reproduction) -> rv.ReplayResult:
    """The ``sandbox_pov`` engine: scope lock, then one isolated run of the stored input."""
    from tools.vulnresearch import targets

    try:
        scoped = targets.assert_in_scope(repro.target)
    except ScopeViolation as exc:
        return rv.ReplayResult(outcome=rv.ReplayOutcome.refused, detail=str(exc))
    step = repro.steps[0]
    spec = dict(step["spec"])
    root = Path(step.get("root_override") or scoped["root"])
    run = run_in_sandbox("pov_runner.py", spec, target_root=root)
    if run["status"] != "completed" or not run["result"]:
        outcome = rv.ReplayOutcome.unavailable if run["status"] == "unavailable" else rv.ReplayOutcome.error
        return rv.ReplayResult(outcome=outcome, detail=f"sandbox {run['status']}: {run['stderr'][-300:]}")
    r = run["result"]
    if not r.get("decisive"):
        return rv.ReplayResult(outcome=rv.ReplayOutcome.error, detail=r.get("detail") or "indecisive",
                               observations=[{"oracle": r.get("oracle"), "exception": r.get("exception")}])
    return rv.ReplayResult(outcome=rv.ReplayOutcome.reproduced if r["fired"] else rv.ReplayOutcome.not_reproduced,
                           detail=r.get("detail") or "",
                           observations=[{"oracle": r.get("oracle"), "fired": r["fired"],
                                          "exception": r.get("exception")}])


def register() -> None:
    rv.register_pov_replayer(_replayer)


def reproduction(target_id: str, spec: dict, *, root_override: Optional[str] = None) -> rv.Reproduction:
    step = {"input": spec.get("which", "pov"), "spec": spec}
    if root_override:
        step["root_override"] = root_override
    return rv.Reproduction(kind="sandbox_pov", target=target_id, steps=[step],
                           predicate={"type": "pov_oracle", "oracle": spec["oracle"]},
                           description=f"{spec['module']}:{spec['function']} {spec['oracle']}")


def _spec(c: dict, sealed: dict, which: str, cfg: dict, src_root: str) -> dict:
    inp = sealed[which]
    return {"which": which, "module": c["module"], "function": c["function"], "src_root": src_root,
            "input": inp["input"], "input_encoding": inp["input_encoding"], "oracle": sealed["oracle"],
            "canary": sealed["canary"], "token": sealed["token"], "plant": sealed["plant"],
            "expected_exceptions": (cfg.get("fuzz") or {}).get("expected_exceptions") or ["ValueError"],
            "call_timeout": int((cfg.get("sandbox") or {}).get("call_timeout_seconds") or 5)}


def replay_pair(target_id: str, c: dict, sealed: dict, cfg: dict, src_root: str, *,
                against: str = "control", root_override: Optional[str] = None) -> dict:
    """Replay the PoV, then either its benign control (``against='control'``: is this a finding?) or the same PoV
    on another tree (``against='patched'``: does the patch fix it?). The validator decides."""
    register()
    first = rv.replay(reproduction(target_id, _spec(c, sealed, "pov", cfg, src_root)))
    if against == "control":
        second = rv.replay(reproduction(target_id, _spec(c, sealed, "control", cfg, src_root)))
        vocab = rv.POV_VOCABULARY
    else:
        second = rv.replay(reproduction(target_id, _spec(c, sealed, "pov", cfg, src_root),
                                        root_override=root_override))
        vocab = rv.PATCH_VOCABULARY
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
