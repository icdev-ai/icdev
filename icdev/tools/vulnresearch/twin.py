# CUI // SP-CTI
"""What the governance twin reads about vulnerability discovery, and how it judges it (icx-ent-01).

:func:`twin_evidence` reads the ledger (never raising: unreadable is unverified); :func:`twin_judge` is pure, so a
replay of a recorded observation judges the same. The adapter is ``tools/twin_core/adapters/vulnresearch.py``.
Every finding carries a thread to the ledger row it rests on (``vulnresearch:<event id>``).

Checks (an unreported datum is UNVERIFIED, never passed):
* the ledger was read;
* each authorised target has a PUBLISHED findings bundle;
* the pipeline has a benchmark measurement.
"""
from __future__ import annotations

from typing import Any


def twin_evidence() -> dict:
    from tools.vulnresearch import benchmark, bundle, discovery, ledger, patches, targets

    ev: dict[str, Any] = {"targets": None, "errors": {}}
    try:
        cfg = targets.load_config()
        rows = ledger.read()
        ch = ledger.chain(rows)
        tv = targets.targets_view()
    except Exception as exc:  # noqa: BLE001 — unreadable is unverified
        ev["errors"]["ledger"] = str(exc)
        return ev
    items = []
    for t in tv["targets"]:
        tid = t["target_id"]
        rs = discovery.runs(tid)
        newest = rs[0] if rs else None
        bl = bundle.ledger_view(tid)
        ps = patches.patches_for(tid)
        findings = []
        for f in (newest or {}).get("run", {}).get("findings") or []:
            p = (ps.get(f["finding_id"]) or {}).get("proposed")
            findings.append({"finding_id": f["finding_id"], "cwe": f["cwe"], "severity": f.get("severity"),
                             "function": f"{f['module']}:{f['function']}",
                             "patch": None if not p else {"event_id": p["event_id"], "created_at": p["created_at"],
                                                          "decided": (p.get("decided") or {}).get("event")}})
        items.append({"target_id": tid, "name": t["name"], "status": t["status"],
                      "registered": {k: t["registered"][k] for k in ("event_id", "actor", "created_at")},
                      "decision": t["decision"] and {k: t["decision"][k] for k in ("event_id", "event", "actor",
                                                                                   "expires_on", "created_at")},
                      "run": newest and {"event_id": newest["event_id"], "created_at": newest["created_at"],
                                         "isolation": (newest["run"].get("isolation") or {}).get("isolation"),
                                         "waiver": ((newest["run"].get("isolation") or {}).get("waiver") or {})
                                         .get("id")},
                      "findings": findings,
                      "bundle": {"prepared": bl["prepared"] and {
                          "event_id": bl["prepared"]["event_id"], "actor": bl["prepared"]["actor"],
                          "created_at": bl["prepared"]["created_at"], "decided": bool(bl["prepared"].get("decided"))},
                          "published": bl["published"] and {k: bl["published"][k] for k in (
                              "event_id", "actor", "prepared_by", "created_at")}}})
    m = benchmark.latest()
    ev["targets"] = items
    ev["problems"] = ch["_problems"]
    ev["measurement"] = m and {"event_id": m["event_id"], "created_at": m["created_at"],
                               "scores": {k: m["scores"][k] for k in ("false_positive_rate", "false_negative_rate",
                                                                     "tp", "fp", "fn", "tn")},
                               "reviewed_by": m["labels"].get("reviewed_by")}
    ev["policy"] = cfg.get("publish") or {}
    return ev


def twin_judge(ev: dict, policy: dict, observed_at: str) -> tuple[list[dict], list[dict], dict]:
    """``(checks, findings, summary)``. Pure."""
    from tools.twin_core.governance import age_hours, check, finding

    errors = ev.get("errors") or {}
    items = ev.get("targets")
    checks = [check("vulnresearch.ledger", "Vulnerability discovery ledger (vr_events)", items is not None,
                    errors.get("ledger") or "ledger not read")]
    out: list[dict] = []
    pol = {**(ev.get("policy") or {}), **(policy or {})}
    waiting = float(pol.get("waiting_warn_days") or 7)
    stale = float(pol.get("stale_days") or 30)
    for p in ev.get("problems") or []:
        out.append(finding("vulnresearch.chain_broken", "critical", f"{p['stream']}: {p['problem']}",
                           thread_id=f"vulnresearch:{p['event_id']}", category="security",
                           recommendation="The ledger is append-only: find who wrote the row and treat it as an "
                                          "incident."))
    m = ev.get("measurement")
    if items is not None:
        checks.append(check("vulnresearch.measurement", "A false-positive / false-negative measurement on the "
                            "labelled benchmark", m is not None, "no measurement recorded: the rates are unverified"))
    if m and not m.get("reviewed_by"):
        out.append(finding("vulnresearch.labels_unreviewed", "low",
                           "The benchmark's labels are planted by construction and no person has reviewed them",
                           thread_id=f"vulnresearch:{m['event_id']}", category="security",
                           recommendation="A person reviews context/vulnresearch/benchmark/labels.yaml and signs "
                                          "reviewed_by."))
    summary = {"targets": 0, "authorised": 0, "findings": 0, "unpatched": 0, "published": 0,
               "false_positive_rate": (m or {}).get("scores", {}).get("false_positive_rate"),
               "false_negative_rate": (m or {}).get("scores", {}).get("false_negative_rate")}
    for t in items or []:
        summary["targets"] += 1
        tid, reg, dec = t["target_id"], t["registered"], t.get("decision")
        if t["status"] == "awaiting_authorisation":
            age = age_hours(reg["created_at"], observed_at)
            if age is not None and age > waiting * 24:
                out.append(finding("vulnresearch.awaiting_authorisation", "low",
                                   f"{t['name']}: registered {age / 24:.0f} days ago and not decided",
                                   thread_id=f"vulnresearch:{reg['event_id']}", category="security",
                                   recommendation="A person who did not register it authorises it or refuses."))
            continue
        if not dec or dec["event"] != "authorised":
            continue
        summary["authorised"] += 1
        if dec.get("expires_on") and observed_at[:10] > dec["expires_on"]:
            out.append(finding("vulnresearch.authorisation_expired", "medium",
                               f"{t['name']}: the authorisation to test it lapsed on {dec['expires_on']}",
                               thread_id=f"vulnresearch:{dec['event_id']}", category="security",
                               recommendation="Register the target again for a new authorisation, or leave it."))
        pub = (t.get("bundle") or {}).get("published")
        checks.append(check(f"vulnresearch.bundle.{tid}", f"A published findings bundle for {t['name']}",
                            pub is not None, "no findings bundle of this target has been published: unverified"))
        run = t.get("run")
        if run and run.get("isolation") == "container":
            if run.get("waiver"):
                out.append(finding("vulnresearch.isolation_waived", "medium",
                                   f"{t['name']}: ran in a plain container (runc) under the named waiver "
                                   f"{run['waiver']}, not gVisor or a microVM",
                                   thread_id=f"vulnresearch:{run['event_id']}", category="security",
                                   recommendation="Owner step: install gVisor (runsc) or a microVM runtime into "
                                                  "the engine and set sandbox.isolated.oci_runtime."))
            else:
                # A blocking condition: run_isolated refuses this, so a recorded run like it is an incident.
                out.append(finding("vulnresearch.isolation_container", "critical",
                                   f"{t['name']}: target code ran in a plain container with no waiver",
                                   thread_id=f"vulnresearch:{run['event_id']}", category="security",
                                   recommendation="Treat the run as untrusted and find how it bypassed "
                                                  "SandboxExecutor.run_isolated's isolation gate."))
        for f in t.get("findings") or []:
            summary["findings"] += 1
            patch = f.get("patch")
            if not patch or patch.get("decided") != "patch_accepted":
                summary["unpatched"] += 1
                out.append(finding("vulnresearch.finding_unpatched", "high" if f.get("severity") in (
                    "critical", "high") else "medium",
                    f"{t['name']}: {f['cwe']} in {f['function']} is confirmed by a PoV and has no accepted patch",
                    thread_id=f"vulnresearch:{run['event_id']}", category="security",
                    recommendation="Propose a patch whose PoV fails and whose tests pass; a person reviews it."))
            if patch and not patch.get("decided"):
                age = age_hours(patch["created_at"], observed_at)
                if age is not None and age > waiting * 24:
                    out.append(finding("vulnresearch.patch_awaiting_review", "low",
                                       f"{t['name']}: a patch for {f['function']} has waited {age / 24:.0f} days",
                                       thread_id=f"vulnresearch:{patch['event_id']}", category="security",
                                       recommendation="A person who did not propose it accepts or rejects it."))
        b = t.get("bundle") or {}
        prep = b.get("prepared")
        if prep and not prep.get("decided"):
            age = age_hours(prep["created_at"], observed_at)
            if age is not None and age > waiting * 24:
                out.append(finding("vulnresearch.bundle_awaiting_publish", "low",
                                   f"{t['name']}: a prepared findings bundle has waited {age / 24:.0f} days",
                                   thread_id=f"vulnresearch:{prep['event_id']}", category="security",
                                   recommendation="A person who did not prepare it publishes it or refuses."))
        if pub:
            summary["published"] += 1
            if pub.get("actor") and pub["actor"] == pub.get("prepared_by"):
                out.append(finding("vulnresearch.self_published", "critical",
                                   f"{t['name']}: the bundle was published by the person who prepared it",
                                   thread_id=f"vulnresearch:{pub['event_id']}", category="security",
                                   recommendation="Treat it as unpublished; a person who did not prepare it "
                                                  "publishes a new one."))
            age = age_hours(pub["created_at"], observed_at)
            if age is not None and age > stale * 24:
                out.append(finding("vulnresearch.bundle_stale", "medium",
                                   f"{t['name']}: the newest published bundle is {age / 24:.0f} days old",
                                   thread_id=f"vulnresearch:{pub['event_id']}", category="security",
                                   recommendation="Run discovery again and publish a new bundle."))
    return checks, out, summary
