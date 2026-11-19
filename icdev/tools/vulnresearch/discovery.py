# CUI // SP-CTI
"""The discovery pipeline: candidates from SAST, SCA, an agent and fuzzing; findings only with a PoV (icx-ent-01).

One run against one AUTHORISED target (``targets.assert_in_scope`` first, so nothing is read or run otherwise):

1. **SAST** -- ``tools/security/sast_runner.run_bandit`` over the tree (static; it never runs target code). A rule
   with a weakness class in ``sast_cwe`` is a candidate at its enclosing top-level function;
2. **SCA** -- ``tools/security/dependency_auditor.audit_python`` (pip-audit) on the target's manifest, with each
   advisory flagged when it is in CISA KEV (the table ``tools/strategos/cisa_kev_importer.py`` fills). A dependency
   advisory has no PoV recipe: it is carried as an UNPROVEN advisory, never a finding (ICDEV[OS]'s vulnerability
   twin weighs it by KEV, EPSS and reachability);
3. **agentic source review** -- through Cortex governance (``tools.cortex.api.complete``, the TRUST chain) and the
   LLM router (``llm_function: vuln_source_review``; no model id in code). The model only PROPOSES candidates and
   fuzz entry points; one that names a file or function outside the target is dropped (``out_of_scope``). With no
   model (``ICDEV_NO_LLM``, or none reachable) the step says so and proposes nothing;
4. **fuzzing** -- a generated harness per fuzz entry point, coverage-guided, in the sandbox (``harness.fuzz``);
   a crash is a candidate;
5. **proof** -- each candidate's PoV replayed in the sandbox through reproduction_validator (``pov.replay_pair``).
   A candidate becomes a FINDING only when its PoV discriminates; otherwise it is dropped and counted by reason.

The whole result is recorded, at its digest, as one ``run_recorded`` event on ``run:<id>``. Sealed PoV inputs stay
in that record (``sealed``) and nowhere else: :func:`public_run` strips them from every view.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Optional

from tools.logging.icdev_logger import get_logger
from tools.security.redteam_scope import ScopeViolation
from tools.vulnresearch import harness, ledger, pov, targets
from tools.vulnresearch.constants import DROP_REASONS, FINDING, GENESIS

logger = get_logger("icdev.vulnresearch.discovery")

RunRefused = ledger.LedgerRefused


# ── the target's functions ───────────────────────────────────────────────────


def index(scoped: dict) -> dict:
    """``{rel path: {module, source, functions: {name: {start, end, params}}}}`` for the in-scope Python files."""
    src_root = (scoped["registration"].get("src_root") or ".").strip("/") or "."
    out = {}
    for f in scoped["tree"]["files"]:
        rel = f["path"]
        if not rel.endswith(".py"):
            continue
        under = rel if src_root == "." else (rel[len(src_root) + 1:] if rel.startswith(src_root + "/") else None)
        if under is None:
            continue
        module = under[:-3].replace("/", ".")
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        source = (scoped["root"] / rel).read_text(encoding="utf-8", errors="replace")
        funcs = {}
        try:
            for node in ast.parse(source).body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    a = node.args
                    params = len(a.posonlyargs) + len(a.args) - len(a.defaults)
                    single = (params == 1 and not a.vararg and not a.kwonlyargs) or \
                             (params == 0 and len(a.args) == 1)
                    funcs[node.name] = {"start": node.lineno, "end": getattr(node, "end_lineno", node.lineno),
                                        "harnessable": bool(single and not isinstance(node, ast.AsyncFunctionDef))}
        except SyntaxError:
            pass
        out[rel] = {"module": module, "source": source, "functions": funcs}
    return out


def _enclosing(idx: dict, rel: str, line: int) -> Optional[str]:
    for name, f in (idx.get(rel) or {}).get("functions", {}).items():
        if f["start"] <= line <= f["end"]:
            return name
    return None


def _rel(scoped: dict, path: str) -> str:
    p = Path(path)
    try:
        return (p if p.is_absolute() else Path.cwd() / p).resolve().relative_to(scoped["root"]).as_posix()
    except ValueError:
        return str(path)


# ── the candidate sources ────────────────────────────────────────────────────


def sast(scoped: dict, idx: dict, cfg: dict) -> tuple[dict, list[dict]]:
    from tools.security import sast_runner

    res = sast_runner.run_bandit(str(scoped["root"]))
    status = {"tool": "bandit", "ran": bool(res.get("success")),
              "note": "" if res.get("success") else str(res.get("raw_output") or "")[:300],
              "raw_findings": len(res.get("findings") or [])}
    cands = []
    for f in res.get("findings") or []:
        rel = _rel(scoped, f.get("file", ""))
        fn = _enclosing(idx, rel, int(f.get("line") or 0))
        cands.append({"source": "sast", "rule": f.get("test_id"), "file": rel, "line": int(f.get("line") or 0),
                      "function": fn, "cwe": (cfg.get("sast_cwe") or {}).get(f.get("test_id")),
                      "hypothesis": f.get("issue_text") or ""})
    return status, cands


def _kev(ids: list[str], max_age_days: Optional[float] = None) -> tuple[Optional[set], str]:
    """``(the ids in CISA KEV, why not read)``. ``None`` -- UNVERIFIED, never "not in KEV" -- unless the KEV feed
    (``sg_cve_feed``, filled by tools/strategos/cisa_kev_importer.py) has KEV rows refreshed within
    ``sca.kev_max_age_days``."""
    from datetime import datetime, timezone

    if max_age_days is None:
        max_age_days = float(((targets.load_config().get("sca") or {}).get("kev_max_age_days")) or 7)
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            n, newest = conn.execute("SELECT COUNT(*), MAX(updated_at) FROM sg_cve_feed WHERE is_kev = 1").fetchone()
            if not n:
                return None, "KEV not read: the feed is empty (run tools/strategos/cisa_kev_importer.py)"
            ts = datetime.fromisoformat(str(newest).replace("Z", "+00:00").replace(" ", "T"))
            ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
            if age > max_age_days:
                return None, f"KEV not read: the feed is stale ({age:.0f} days old, limit {max_age_days:.0f})"
            if not ids:
                return set(), ""
            rows = conn.execute(f"SELECT cve_id FROM sg_cve_feed WHERE is_kev = 1 AND cve_id IN "  # nosec B608
                                f"({', '.join(['%s'] * len(ids))})", tuple(ids)).fetchall()
        finally:
            conn.close()
        return {r[0] for r in rows}, ""
    except Exception as exc:  # noqa: BLE001 — no KEV table is unverified, never "not in KEV"
        return None, f"KEV not read ({exc})"


#: A requirement line ICDEV hands to pip-audit: an exact pin, with optional extras and an environment marker.
#: Anything else -- an option (-e, -r, --index-url ...), a URL, a VCS ref, a local path, ``name @ ...`` -- could
#: make pip fetch, build or run the target's code on the host, so the whole manifest is refused.
_PIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._ -]+\])?\s*==\s*[A-Za-z0-9.!+_-]+"
                     r"(\s*;\s*[A-Za-z0-9_.<>=!'\" ()-]+)?$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._ -]+\])?\s*(>=|<=|~=|>|<|!=)")


def vet_requirements(text: str) -> tuple[list[str], str, str]:
    """``(pinned lines, refused why, unpinned why)``. Pure: nothing is installed or read but the text."""
    pins, refused, unpinned = [], "", ""
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip() if not raw.strip().startswith("#") else ""
        if not line:
            continue
        if _PIN_RE.match(line):
            pins.append(line)
        elif _NAME_RE.match(line) and not re.search(r"(://|@|^\.|/|\\)", line):
            unpinned = unpinned or f"line {n} is unpinned ({line[:60]})"
        else:
            refused = refused or f"line {n} is not a plain pinned requirement ({line[:60]})"
    return pins, refused, unpinned


def _pip_audit(argv: list[str]) -> dict:
    """pip-audit, with exactly the arguments given (``--no-deps --disable-pip -r <vetted copy>``)."""
    import subprocess  # nosec B404 — fixed argv, never a shell

    proc = subprocess.run([sys.executable, "-m", "pip_audit", *argv], capture_output=True, text=True,  # nosec B603
                          timeout=300)
    if proc.returncode not in (0, 1) or not proc.stdout.strip():
        raise RuntimeError((proc.stderr or "pip-audit produced no output").strip()[:300])
    return json.loads(proc.stdout)


def sca(scoped: dict) -> tuple[dict, list[dict]]:
    """Dependency audit WITHOUT running any of the target's code on the host.

    Only ``requirements*.txt`` at the target root is read, and only when every line is an exact pin: a vetted copy
    goes to pip-audit with ``--no-deps --disable-pip`` (it resolves nothing and installs nothing; it only asks the
    advisory service about the named versions). An option, URL, VCS ref or path refuses the manifest; an unpinned
    line leaves the audit unverified; a ``pyproject.toml`` alone is never built. Each advisory is UNPROVEN, flagged
    against CISA KEV when that feed is read and fresh.
    """
    import tempfile

    from tools.security import dependency_auditor

    root = Path(scoped["root"])
    status = {"tool": "pip-audit", "ran": False, "refused": False, "advisories": 0, "note": "", "kev_note": "",
              "flags": ["--no-deps", "--disable-pip"]}
    reqs = sorted(p for p in root.glob("requirements*.txt") if p.is_file())
    if not reqs:
        status["note"] = ("no requirements*.txt; a pyproject.toml is never built to resolve it (unverified)"
                          if (root / "pyproject.toml").exists() else "no dependency manifest")
        return status, []
    pins: list[str] = []
    for r in reqs:
        got, refused, unpinned = vet_requirements(r.read_text(encoding="utf-8", errors="replace"))
        if refused:
            status.update(refused=True, note=f"{r.name}: {refused}: the manifest is refused, nothing is audited")
            return status, []
        if unpinned:
            status["note"] = f"{r.name}: {unpinned}: unverified (only exact pins are audited)"
            return status, []
        pins += got
    with tempfile.TemporaryDirectory(prefix="vr-sca-") as d:
        vetted = Path(d) / "requirements.txt"
        vetted.write_text("\n".join(pins) + "\n", encoding="utf-8")
        try:
            data = _pip_audit(["-r", str(vetted), "--no-deps", "--disable-pip", "-f", "json", "--progress-spinner",
                               "off"])
        except Exception as exc:  # noqa: BLE001 — not audited is unverified, never clean
            status["note"] = f"pip-audit did not run: {exc}"[:300]
            return status, []
    findings = dependency_auditor._parse_pip_audit_json(json.dumps(data))
    ids = sorted({a for f in findings for a in [f.get("vulnerability_id"), *(f.get("aliases") or [])]
                  if str(a or "").startswith("CVE-")})
    kev, why = _kev(ids)
    adv = []
    for f in findings:
        cves = [a for a in [f.get("vulnerability_id"), *(f.get("aliases") or [])] if str(a or "").startswith("CVE-")]
        adv.append({"package": f.get("package"), "version": f.get("version"), "id": f.get("vulnerability_id"),
                    "cves": cves, "fix_versions": f.get("fix_versions") or [],
                    "kev": None if kev is None else bool(set(cves) & kev), "status": "unproven"})
    status.update(ran=True, advisories=len(adv), kev_note=why)
    return status, adv


def _no_model() -> bool:
    return os.environ.get("ICDEV_NO_LLM", "").lower() in ("1", "true", "yes")


def agent_review(scoped: dict, idx: dict, cfg: dict, sast_cands: list[dict], actor: dict) -> tuple[dict, list[dict]]:
    """Cortex-governed source review. Proposes; never confirms. ``(status, candidates)``."""
    rc = cfg.get("review") or {}
    if _no_model():
        return {"ran": False, "note": "no model (ICDEV_NO_LLM): the agentic review proposed nothing"}, []
    budget = int(rc.get("max_source_bytes") or 60000)
    parts, used = [], 0
    for rel, info in sorted(idx.items()):
        chunk = f"### {rel}\n{info['source']}\n"
        if used + len(chunk) > budget:
            break
        parts.append(chunk)
        used += len(chunk)
    tmpl = (targets.BASE_DIR / "hardprompts" / "vulnresearch" / "source_review.md").read_text(encoding="utf-8")
    prompt = tmpl.format(cwes=", ".join(sorted((cfg.get("recipes") or {}).keys())),
                         sast=json.dumps([{k: c[k] for k in ("file", "line", "rule", "cwe")} for c in sast_cands]),
                         source="".join(parts))
    try:
        from tools.cortex import api
        from tools.cortex.schemas import CortexContext

        ctx = CortexContext(user_id=str(actor.get("id") or ""), classification="CUI", agent_id="vulnresearch")
        result = api.complete(prompt, function=str(rc.get("llm_function") or "vuln_source_review"), ctx=ctx)
        text = str(getattr(result, "text", "") or getattr(result, "content", "") or "")
    except Exception as exc:  # noqa: BLE001 — a review that did not run proposed nothing, and says why
        return {"ran": False, "note": f"the agentic review did not run: {type(exc).__name__}: {exc}"[:300]}, []
    m = re.search(r"\[.*\]", text, re.S)
    try:
        items = json.loads(m.group(0)) if m else []
    except ValueError:
        items = []
    cands = []
    for it in items[: int(rc.get("max_candidates") or 20)]:
        if not isinstance(it, dict):
            continue
        rel = str(it.get("file") or "")
        fn = str(it.get("function") or "")
        line = (idx.get(rel) or {}).get("functions", {}).get(fn, {}).get("start", 0)
        cands.append({"source": "agent", "rule": "agent-review", "file": rel, "line": line, "function": fn,
                      "cwe": "CWE-20" if it.get("fuzz") else str(it.get("cwe") or ""),
                      "fuzz": bool(it.get("fuzz")), "hypothesis": str(it.get("hypothesis") or "")[:300],
                      "provenance": "modelled"})
    return {"ran": True, "proposed": len(cands), "provenance": "modelled",
            "model": getattr(result, "model", None)}, cands


# ── proving ──────────────────────────────────────────────────────────────────


def finding_id(target_id: str, tree_digest: str, module: str, function: str, cwe: str) -> str:
    """Stable across runs of one target at one tree; never shared by two targets (each has its own patch stream)."""
    return "vrf-" + ledger.digest([target_id, tree_digest, module, function, cwe])[:12]


def _locate(c: dict, idx: dict) -> Optional[str]:
    """None when the candidate names a real, harnessable function of the target; else why it is dropped."""
    info = idx.get(c.get("file") or "")
    if info is None:
        return "out_of_scope"
    if not c.get("function"):
        return "no_harness"        # module-level code: there is no entry point to call
    if c["function"] not in info["functions"]:
        return "out_of_scope"
    if not info["functions"][c["function"]]["harnessable"]:
        return "no_harness"
    return None


def run(target_id: str, actor: Optional[dict], *, use_model: bool = True, conn=None) -> dict:
    """Run discovery on an authorised target and record the result. Raises :class:`RunRefused`."""
    if not isinstance(actor, dict) or not actor.get("id"):
        raise RunRefused(401, "no_actor", "a discovery run needs a signed-in person or a named agent")
    try:
        scoped = targets.assert_in_scope(target_id, conn)
    except ScopeViolation as exc:
        raise RunRefused(403, "out_of_scope", str(exc)) from exc
    gate = pov.isolation_decision(scoped)
    if not gate["allowed"]:
        raise RunRefused(409, "isolation", gate["why"])
    cfg = targets.load_config()
    reg = scoped["registration"]
    src_root = reg.get("src_root") or "."
    idx = index(scoped)
    sast_status, sast_cands = sast(scoped, idx, cfg)
    sca_status, advisories = sca(scoped)
    if use_model:
        review_status, agent_cands = agent_review(scoped, idx, cfg, sast_cands, actor)
    else:
        review_status, agent_cands = {"ran": False, "note": "not asked for on this run"}, []

    # Fuzz entry points: declared at registration, plus the agent's proposals that are real functions.
    entries = []
    for ft in reg.get("fuzz_targets") or []:
        mod, _, fn = ft.partition(":")
        rel = next((r for r, i in idx.items() if i["module"] == mod), None)
        entries.append({"module": mod, "function": fn, "file": rel, "why": "declared at registration"})
    for c in agent_cands:
        if c.get("fuzz") and not _locate(c, idx):
            entries.append({"module": idx[c["file"]]["module"], "function": c["function"], "file": c["file"],
                            "why": "proposed by the agentic review"})
    fuzz_runs, fuzz_cands, seen_entries = [], [], set()
    for e in entries:
        key = (e["module"], e["function"])
        if key in seen_entries:
            continue
        seen_entries.add(key)
        if not e["file"] or e["function"] not in idx[e["file"]]["functions"] or \
                not idx[e["file"]]["functions"][e["function"]]["harnessable"]:
            fuzz_runs.append({**e, "status": "not_run", "detail": "not a harnessable function of the target"})
            continue
        seeds = (reg.get("fuzz_seeds") or {}).get(f"{e['module']}:{e['function']}") or ["", "a"]
        fr = harness.fuzz(target_id, scoped["root"], e, cfg, scoped=scoped, src_root=src_root, seeds=seeds,
                          source=idx[e["file"]]["source"])
        fuzz_runs.append({**e, **{k: fr[k] for k in ("status", "engine", "crashed", "exception", "iterations",
                                                     "coverage", "image", "harness_digest")}})
        if fr["crashed"] and fr["crash_input"]:
            fuzz_cands.append({"source": "fuzz", "rule": f"fuzz:{fr['engine']}", "file": e["file"],
                               "line": idx[e["file"]]["functions"][e["function"]]["start"],
                               "function": e["function"], "cwe": "CWE-20",
                               "hypothesis": f"{fr['exception']} escapes {e['function']} on a fuzz input",
                               "crash_input": fr["crash_input"], "seed": seeds[0] if seeds else ""})

    # Merge candidates per (file, function, weakness): one proof each, every source credited.
    merged: dict[tuple, dict] = {}
    for c in sast_cands + [a for a in agent_cands if not a.get("fuzz")] + fuzz_cands:
        k = (c.get("file"), c.get("function"), c.get("cwe"))
        if k in merged:
            m = merged[k]
            m["sources"] = sorted(set(m["sources"]) | {c["source"]})
            m["rules"] = sorted(set(m["rules"]) | {str(c.get("rule"))})
            if c.get("crash_input") and not m.get("crash_input"):
                m.update(crash_input=c["crash_input"], seed=c.get("seed", ""))
        else:
            merged[k] = {**c, "sources": [c["source"]], "rules": [str(c.get("rule"))]}

    findings, dropped, sealed_all = [], [], {}
    recipes = cfg.get("recipes") or {}
    for c in merged.values():
        base = {k: c.get(k) for k in ("file", "line", "function", "cwe", "hypothesis", "sources", "rules")}
        why = _locate(c, idx)
        if why is None and not recipes.get(c.get("cwe") or ""):
            why = "no_recipe"
        if why is not None:
            dropped.append({**base, "reason": why})
            continue
        module = idx[c["file"]]["module"]
        fid = finding_id(target_id, reg["tree_digest"], module, c["function"], c["cwe"])
        recipe = recipes[c["cwe"]]
        sealed = pov.materialise(recipe, fid, crash_input=c.get("crash_input"), seed_input=c.get("seed"))
        cand = {"module": module, "function": c["function"]}
        proof = pov.replay_pair(target_id, cand, sealed, cfg, src_root, against="control")
        reason = pov.drop_reason(proof)
        replays = {"pov": proof["first"]["outcome"], "control": proof["second"]["outcome"],
                   "reason": proof["reason"]}
        if reason:
            dropped.append({**base, "reason": reason, "replays": replays})
            continue
        sealed_all[fid] = sealed
        findings.append({**base, "finding_id": fid, "module": module, "status": FINDING,
                         "severity": recipe.get("severity") or "high", "weakness": recipe.get("name"),
                         "pov": {"digest": pov.sealed_digest(sealed), "oracle": sealed["oracle"],
                                 "recipe": c["cwe"], "sealed": True, "replays": replays,
                                 "kind": "sandbox_pov"}})
    from tools.security.sandbox_executor import isolated_profile

    counts = {"candidates": len(merged), "findings": len(findings), "dropped": len(dropped),
              "dropped_by_reason": {r: sum(1 for d in dropped if d["reason"] == r) for r in DROP_REASONS},
              "by_source": {s: sum(1 for c in merged.values() if s in c["sources"])
                            for s in ("sast", "agent", "fuzz")},
              "advisories": len(advisories)}
    rid = f"vrr-{uuid.uuid4().hex[:10]}"
    content = {"run_id": rid, "target_id": target_id, "tree_digest": reg["tree_digest"], "commit": reg.get("commit"),
               "config_sha256": cfg["_sha256"], "isolation": {**{k: v for k, v in isolated_profile().items() if k != "flags"},
                             "waiver": gate["waiver"]},
               "sast": sast_status, "sca": sca_status, "review": review_status, "fuzz": fuzz_runs,
               "findings": sorted(findings, key=lambda f: f["finding_id"]),
               "dropped": sorted(dropped, key=lambda d: (str(d.get("file")), str(d.get("function")),
                                                         str(d.get("cwe")))),
               "advisories": advisories, "counts": counts, "sealed": sealed_all}
    own = conn is None
    conn = conn or ledger.connection()
    try:
        row = ledger.append(conn, stream=f"run:{rid}", event="run_recorded", subject=target_id,
                            actor=str(actor["id"]), actor_kind=ledger.actor_kind(actor),
                            actor_role=str(actor.get("role") or ""), content=content, prev_hash=GENESIS)
    finally:
        if own:
            conn.close()
    return {"run_id": rid, "event_id": row["event_id"], "content_digest": row["content_digest"],
            "run": public_run(content)}


def public_run(content: dict) -> dict:
    """A run as every view shows it: the sealed PoV inputs withheld."""
    return {k: v for k, v in content.items() if k != "sealed"}


def runs(target_id: Optional[str] = None, conn=None) -> list[dict]:
    """Every recorded run (newest first), sealed inputs withheld, each with its event and actor."""
    own = conn is None
    conn = conn or ledger.connection()
    try:
        ch = ledger.chain(ledger.read(conn, prefix="run:"))
    finally:
        if own:
            conn.close()
    out = []
    for stream, ordered in ch.items():
        if stream == "_problems":
            continue
        for r in ordered:
            if r["event"] != "run_recorded" or (target_id and r["subject"] != target_id):
                continue
            out.append({"event_id": r["event_id"], "actor": r["actor"], "actor_kind": r["actor_kind"],
                        "created_at": r["created_at"], "content_digest": r["content_digest"],
                        "run": public_run(ledger.payload(r).get("content") or {})})
    return sorted(out, key=lambda x: x["created_at"], reverse=True)


def sealed_for(finding_id_: str, conn=None) -> Optional[tuple[dict, dict]]:
    """``(finding, sealed PoV)`` from the newest run that confirmed it. For the replay engine only."""
    own = conn is None
    conn = conn or ledger.connection()
    try:
        rows = ledger.read(conn, prefix="run:")
    finally:
        if own:
            conn.close()
    for r in sorted(rows, key=lambda x: x["created_at"], reverse=True):
        c = ledger.payload(r).get("content") or {}
        if finding_id_ in (c.get("sealed") or {}):
            f = next(x for x in c["findings"] if x["finding_id"] == finding_id_)
            return {**f, "target_id": c["target_id"], "run_id": c["run_id"]}, c["sealed"][finding_id_]
    return None
