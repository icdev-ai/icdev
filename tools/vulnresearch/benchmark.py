# CUI // SP-CTI
"""False-positive and false-negative rates on a labelled benchmark of planted code (icx-ent-01).

The benchmark (``context/vulnresearch/benchmark``, SYNTHETIC) is seven modules carrying one planted weakness each
and five safe look-alikes; its ground truth is ``labels.yaml``, labelled by construction and awaiting a person's
review (it says so, and so does every measurement). NO MODEL JUDGES: :func:`score` compares the confirmed findings
of a discovery run with the labels, and nothing else.

A measurement is itself governed: the benchmark is a TARGET like any other (registered, authorised by a person,
scope-locked), the run is an ordinary discovery run, and the scores are recorded at their digest as ``measured`` on
that run's stream, so the bundle and the twin cite the exact run they came from.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

import yaml

from tools.vulnresearch import discovery, ledger, targets


def load_labels(cfg: Optional[dict] = None) -> dict:
    cfg = cfg or targets.load_config()
    p = targets.BASE_DIR / ((cfg.get("benchmark") or {}).get("labels") or "context/vulnresearch/benchmark/labels.yaml")
    raw = p.read_bytes()
    labels = yaml.safe_load(raw.decode("utf-8")) or {}
    labels["_sha256"] = hashlib.sha256(raw).hexdigest()
    labels["_path"] = p.relative_to(targets.BASE_DIR).as_posix()
    return labels


def pins_check(cfg: Optional[dict] = None) -> dict:
    """Do the labels and the committed reference fixes still hash to their pins? (benchmark-digest --check)"""
    import shutil
    import subprocess  # nosec B404 — git apply on a throwaway copy, fixed arguments
    import tempfile

    cfg = cfg or targets.load_config()
    b = cfg.get("benchmark") or {}
    labels_now = load_labels(cfg)["_sha256"]
    root, _pin = targets.benchmark_pin(cfg)
    got: dict = {}
    base = {x["path"]: x["sha256"] for x in targets.tree(root, cfg)["files"]}
    fixes = targets.BASE_DIR / str(b.get("fixes_dir") or "context/vulnresearch/benchmark_fixes")
    for diff in sorted(fixes.glob("*.diff")):
        with tempfile.TemporaryDirectory(prefix="vr-fix-") as d:
            copy = Path(d) / "t"
            shutil.copytree(root, copy, ignore=shutil.ignore_patterns("__pycache__"))
            subprocess.run(["git", "apply", str(diff)], cwd=copy, check=True, capture_output=True,  # nosec B603 B607
                           timeout=30)
            for f in targets.tree(copy, cfg)["files"]:
                if f["sha256"] != base.get(f["path"]):
                    got.setdefault(f["path"], []).append(f["sha256"])
    pinned = {k: sorted(v) for k, v in (b.get("reference_fixes") or {}).items()}
    return {"labels_sha256": labels_now, "labels_match": labels_now == b.get("labels_sha256"),
            "reference_fixes": {k: sorted(v) for k, v in got.items()},
            "reference_fixes_match": {k: sorted(v) for k, v in got.items()} == pinned}


def score(findings: list[dict], labels: dict) -> dict:
    """TP/FP/FN/TN and the two rates. Pure: confirmed findings against the labels."""
    expected = {(c["module"], v["function"], v["cwe"]) for c in labels["cases"] for v in c.get("vulnerable") or []}
    safe = {c["module"] for c in labels["cases"] if not c.get("vulnerable")}
    got = {(f["module"], f["function"], f["cwe"]) for f in findings}
    tp = sorted(expected & got)
    fn = sorted(expected - got)
    fp = sorted(got - expected)
    tn = sorted(m for m in safe if not any(g[0] == m for g in got))
    fpr = len(fp) / (len(fp) + len(tn)) if (fp or tn) else None
    fnr = len(fn) / (len(tp) + len(fn)) if (tp or fn) else None
    return {"tp": len(tp), "fp": len(fp), "fn": len(fn), "tn": len(tn),
            "false_positive_rate": None if fpr is None else round(fpr, 4),
            "false_negative_rate": None if fnr is None else round(fnr, 4),
            "true_positives": [":".join(x) for x in tp], "false_negatives": [":".join(x) for x in fn],
            "false_positives": [":".join(x) for x in fp], "true_negatives": tn,
            "judged_by": "labels (planted by construction); no model judges"}


def unverified_why(run: dict) -> str:
    """Why a run cannot be scored ('' when it can): static analysis or the dependency audit did not run, a fuzz
    harness did not complete, or a candidate was dropped because the sandbox could not decide it."""
    why = []
    if not (run.get("sast") or {}).get("ran"):
        why.append(f"static analysis ({(run.get('sast') or {}).get('tool') or 'bandit'}) did not run")
    if not (run.get("sca") or {}).get("ran"):
        why.append(f"the dependency audit ({(run.get('sca') or {}).get('tool') or 'pip-audit'}) did not run")
    for z in run.get("fuzz") or []:
        if z.get("status") != "completed":
            why.append(f"the fuzz harness for {z.get('module')}:{z.get('function')} did not complete")
    by = (run.get("counts") or {}).get("dropped_by_reason") or {}
    for reason in ("sandbox_unavailable", "indecisive"):
        if by.get(reason):
            why.append(f"{by[reason]} candidate(s) dropped as {reason}")
    return "; ".join(why)


def measure(target_id: str, actor: Optional[dict], *, use_model: bool = True, conn=None) -> dict:
    """Run discovery on the registered benchmark target and record its scores against the labels."""
    cfg = targets.load_config()
    labels = load_labels(cfg)
    st = targets.state(target_id, conn)
    root = st["registered"]["registration"]["root"]
    bench = (cfg.get("benchmark") or {}).get("path") or "context/vulnresearch/benchmark"
    if Path(root).as_posix().rstrip("/") != bench.rstrip("/"):
        raise ledger.LedgerRefused(400, "not_the_benchmark", f"{target_id} is {root}, not the benchmark ({bench})")
    out = discovery.run(target_id, actor, use_model=use_model, conn=conn)
    run = out["run"]
    s = score(run["findings"], labels)
    why = unverified_why(run)
    pinned = (cfg.get("benchmark") or {}).get("labels_sha256")
    if labels["_sha256"] != pinned:
        why = "; ".join(x for x in (why, "the labels do not hash to their pin (benchmark.labels_sha256)") if x)
    if why:
        # The counts are kept for the record; the RATES are not claimed: a tool that did not run, or a candidate
        # the sandbox could not decide, would otherwise read as a false negative (or a missing false positive).
        s = {**s, "false_positive_rate": None, "false_negative_rate": None}
    content = {"run_id": out["run_id"], "run_event": out["event_id"], "run_digest": out["content_digest"],
               "target_id": target_id, "tree_digest": run["tree_digest"], "labels": {
                   "path": labels["_path"], "sha256": labels["_sha256"], "synthetic": bool(labels.get("synthetic")),
                   "labelled_by": labels.get("labelled_by"), "reviewed_by": labels.get("reviewed_by"),
                   # Rates are measured "against unreviewed labels" until a person signs reviewed_by (a warning).
                   "reviewed": bool(labels.get("reviewed_by"))},
               "model_used": bool(run["review"].get("ran")), "scores": s, "counts": run["counts"],
               "status": "unverified" if why else "measured", "why": why}
    own = conn is None
    conn = conn or ledger.connection()
    try:
        ordered, _ = ledger.stream_rows(f"run:{out['run_id']}", conn)
        row = ledger.append(conn, stream=f"run:{out['run_id']}", event="measured", subject=target_id,
                            actor=str(actor["id"]), actor_kind=ledger.actor_kind(actor),
                            actor_role=str(actor.get("role") or ""), content=content, prev_hash=ledger.head(ordered))
    finally:
        if own:
            conn.close()
    return {"event_id": row["event_id"], "content_digest": row["content_digest"], **content}


def latest(conn=None) -> Optional[dict]:
    """The newest recorded measurement, with its event."""
    own = conn is None
    conn = conn or ledger.connection()
    try:
        rows = [r for r in ledger.read(conn, prefix="run:") if r["event"] == "measured"]
    finally:
        if own:
            conn.close()
    if not rows:
        return None
    r = max(rows, key=lambda x: x["created_at"])
    return {"event_id": r["event_id"], "created_at": r["created_at"], "actor": r["actor"],
            "content_digest": r["content_digest"], **(ledger.payload(r).get("content") or {})}
