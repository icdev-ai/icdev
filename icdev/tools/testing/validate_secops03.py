# CUI // SP-CTI
"""SECOPS Module 3 validation script.

Run: python tools/testing/validate_secops03.py
"""
from datetime import date
import builtins

import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))


SCAN_RESULTS = {
    ("ICDEV-Prod","IA-2"):{"status":"pass","scanner":"Nessus","date":"2026-04-30"},
    ("ICDEV-Prod","AC-2"):{"status":"pass","scanner":"Nessus","date":"2026-04-30"},
    ("ICDEV-Prod","AU-2"):{"status":"pass","scanner":"Nessus","date":"2026-04-30"},
    ("ICDEV-Dev","IA-2"):{"status":"fail","scanner":"Nessus","date":"2026-04-30","finding":"V-220706: Internal MFA not enforced (CAT II)"},
    ("ICDEV-Dev","AC-2"):{"status":"pass","scanner":"Nessus","date":"2026-04-28"},
    ("ICDEV-Dev","AU-2"):{"status":"fail","scanner":"Nessus","date":"2026-04-28","finding":"V-220739: Audit log retention < 3 years (CAT III)"},
}
POLICY_DOCS = {
    "IA-2":{"doc":"SSP Section 3.5.1","status":"current","last_review":"2026-03-01"},
    "AC-2":{"doc":"SSP Section 3.1.2","status":"current","last_review":"2026-03-01"},
    "AU-2":{"doc":"SSP Section 3.3.1","status":"outdated","last_review":"2024-12-01"},
}
CONTROL_WEIGHTS = {"IA-2":1.0,"AC-2":0.9,"AU-2":0.8}

def collect_scan_evidence(system, control):
    r = SCAN_RESULTS.get((system, control))
    if r: return {"type":"scan","source":r["scanner"],"status":r["status"],"date":r["date"],"finding":r.get("finding")}
    return {"type":"scan","source":"none","status":"not_scanned","date":None,"finding":None}

def collect_policy_evidence(control):
    p = POLICY_DOCS.get(control)
    if p: return {"type":"policy","document":p["doc"],"status":p["status"],"last_review":p["last_review"]}
    return {"type":"policy","document":None,"status":"missing","last_review":None}

def score_evidence(scan, policy, control):
    w = CONTROL_WEIGHTS.get(control, 1.0)
    ss = scan["status"]; ps = policy["status"]
    if ss == "pass" and ps == "current": base = 1.0
    elif ss == "pass" and ps == "outdated": base = 0.7
    elif ss == "pass" and ps == "missing": base = 0.5
    elif ss == "fail": base = 0.2
    elif ss == "not_scanned" and ps == "current": base = 0.4
    else: base = 0.0
    return base * w

class EvidencePipeline:
    def run(self, systems, controls):
        records = []
        for s in systems:
            for c in controls:
                scan = collect_scan_evidence(s, c)
                policy = collect_policy_evidence(c)
                score = score_evidence(scan, policy, c)
                records.append({"system":s,"control":c,"scan":scan,"policy":policy,"score":score,"compliant":score>=0.7})
        avg = round(sum(r["score"] for r in records)/len(records), 3) if records else 0.0
        return {"run_date":str(date.today()),"systems":systems,"controls":controls,"records":records,
                "summary":{"total":len(records),"compliant":sum(1 for r in records if r["compliant"]),
                           "non_compliant":sum(1 for r in records if not r["compliant"]),"avg_score":avg}}

g = {"__builtins__":builtins,"SCAN_RESULTS":SCAN_RESULTS,"POLICY_DOCS":POLICY_DOCS,"CONTROL_WEIGHTS":CONTROL_WEIGHTS,
     "collect_scan_evidence":collect_scan_evidence,"collect_policy_evidence":collect_policy_evidence,
     "score_evidence":score_evidence,"EvidencePipeline":EvidencePipeline,"date":date}
with open(r"apps\forge_academy\content\tier2\m-secops-03-evidence-pipeline\steps\step1_test.py", encoding="utf-8") as f:
    exec(f.read(), g)  # nosec B102 — executes a known test script in a restricted namespace; path is hardcoded
