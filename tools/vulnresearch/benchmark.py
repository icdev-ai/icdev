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

from pathlib import Path
from typing import Optional

import yaml

from tools.vulnresearch import discovery, ledger, targets


def load_labels(cfg: Optional[dict] = None) -> dict:
    cfg = cfg or targets.load_config()
    p = targets.BASE_DIR / ((cfg.get("benchmark") or {}).get("labels") or "context/vulnresearch/benchmark/labels.yaml")
    raw = p.read_bytes()
    labels = yaml.safe_load(raw.decode("utf-8")) or {}
    labels["_sha256"] = ledger.digest(raw.decode("utf-8"))
    labels["_path"] = p.relative_to(targets.BASE_DIR).as_posix()
    return labels


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
    content = {"run_id": out["run_id"], "run_event": out["event_id"], "run_digest": out["content_digest"],
               "target_id": target_id, "tree_digest": run["tree_digest"], "labels": {
                   "path": labels["_path"], "sha256": labels["_sha256"], "synthetic": bool(labels.get("synthetic")),
                   "labelled_by": labels.get("labelled_by"), "reviewed_by": labels.get("reviewed_by")},
               "model_used": bool(run["review"].get("ran")), "scores": s, "counts": run["counts"]}
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
