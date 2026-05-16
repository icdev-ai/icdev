# CUI // SP-CTI
"""Intent classifier — maps a user message to a chat canvas mode.

Fast path: keyword rules.
Fallback: LLM via tools.llm.router when confidence is low.

Returns:
    {
        "mode": "intake" | "cam" | "ndc" | "sdc" | "eda" | "ddc" | "pdc" | "bdc" | "odc" | "idc",
        "canvas_type": str | None,   # None when mode == "intake"
        "confidence": float,         # 0.0–1.0
        "reason": str
    }
"""
from __future__ import annotations

import re
from typing import Any

CANVAS_MODES = {"cam", "ndc", "sdc", "eda", "ddc", "pdc", "bdc", "odc", "idc"}
INTAKE_MODE = "intake"

# Each entry: (canvas_type, score_weight, [keyword_patterns])
# Patterns are matched case-insensitively against the full message.
_RULES: list[tuple[str, float, list[str]]] = [
    # --- Migration / CAM ---
    ("cam", 0.95, [
        r"\bmigrat",
        r"\brefactor\b",
        r"\borganic\s+to\s+cloud\b",
        r"\bmove\s+to\s+(aws|azure|gcp|cloud)",
        r"\bcloud\s+migration\b",
        r"\beol\b",
        r"\bend.of.life\b",
        r"\bdeprecated?\b",
        r"\bsunsett?",
        r"\blegacy\s+(app|system|code|database|db)\b",
        r"\bstrangler\s+fig\b",
        r"\b7rs?\b",
        r"\blift.and.shift\b",
        r"\boracle\b",
        r"\boracle\s+to\b",
        r"\bmigrate.*\b(oracle|mysql|postgres|mongodb|sql.server|elasticsearch)\b",
        r"\b(oracle|mysql|elasticsearch|ruby|java\s*[678])\b.*\b(migrat|upgrad|deprecat|eol)\b",
        r"\bpostgres\s+migration\b",
        r"\bmysql\s+to\b",
        r"\bjava\s*8\b.*migrat",
        r"\bspring\s+boot\s+\d",
        r"\bmoderniz",
        r"\bupgrad.*database\b",
        r"\bcoa\b",
        r"\bcourse.*of.*action\b",
        r"\brefactor.*job\b",
    ]),

    # --- Network Design / NDC ---
    ("ndc", 0.90, [
        r"\bnetwork\s+(topology|design|diagram|segment)\b",
        r"\bnetwork\s+architecture\b.*\b(firewall|vlan|router|subnet|dmz|acl)\b",
        r"\bfirewall\s+(design|rule|policy|architecture)\b",
        r"\bvlan\s+(design|segmentation|trunking|tagging)\b",
        r"\bsubnet\s+(design|mask|cidr|allocation)\b",
        r"\brouter\s+(config|design|protocol|bgp|ospf)\b",
        r"\bload\s+balanc.*\b(design|architect|config)\b",
        r"\bdns\s+(config|design|setup)\b",
        r"\bvpn\b.*design",
        r"\bppsm\b",
        r"\bnetwork\s+access\s+control\b",
        r"\bwireless\s+(design|architecture)\b",
        r"\blan\b.*design",
        r"\bwan\b.*design",
        r"\bsdwan\b",
        r"\bzero.trust.network\b",
        r"\bnetwork\s+segmentation\b",
        r"\bblast\s+radius\b",
        r"\bredundancy\b.*\b(network|wan|link)\b",
        r"\bnetwork\s+(topology|map|diagram)\b",
        r"\b(bgp|ospf|eigrp|isis)\s+(routing|design|peering)\b",
        r"\bdmz\s+(design|architecture|zone)\b",
    ]),

    # --- Security Design / SDC ---
    ("sdc", 0.90, [
        r"\bsecurity\s+(architecture|design|model)\b",
        r"\bthreat\s+model",
        r"\bapi\s+security\b",
        r"\bauthentication\s+design\b",
        r"\bauthorization\s+(model|design|flow)\b",
        r"\biam\s+(design|architecture)\b",
        r"\boauth\b.*design",
        r"\boidc\b.*design",
        r"\bsecurity\s+control",
        r"\bnist\s+800",
        r"\bstig\s+design\b",
        r"\bfips\b.*design",
        r"\bzero\s+trust\s+(architecture|design)\b",
        r"\bpentest\s+architecture\b",
        r"\bsecurity\s+hardening\b",
        r"\bencryption\s+design\b",
        r"\bpki\s+(design|architecture)\b",
    ]),

    # --- Enterprise Data Architecture / EDA ---
    ("eda", 0.88, [
        r"\bdata\s+pipeline\b",
        r"\bevent\s+(streaming|driven|catalog)\b",
        r"\bkafka\b",
        r"\bdata\s+flow\b",
        r"\betl\b",
        r"\belt\b",
        r"\bdata\s+ingestion\b",
        r"\bdata\s+lake\b",
        r"\bdata\s+warehouse\s+design\b",
        r"\bstream\s+processing\b",
        r"\bmessage\s+bus\b",
        r"\bpub.sub\b",
        r"\bapache\s+(spark|flink|beam)\b",
        r"\bdata\s+architecture\b",
        r"\bevent\s+sourcing\b",
        r"\bcqrs\b",
    ]),

    # --- Database Design / DDC ---
    ("ddc", 0.88, [
        r"\bdatabase\s+(schema|design|model)\b",
        r"\ber\s+diagram\b",
        r"\bentity.relationship\b",
        r"\bdata\s+model\b",
        r"\bnormali[sz]ation\b",
        r"\bindex\s+(strategy|design)\b",
        r"\bsharding\b",
        r"\bpartition\s+(strategy|design)\b",
        r"\bsql\s+schema\b",
        r"\btable\s+design\b",
        r"\bnosql\s+design\b",
        r"\bdocument\s+(model|store)\b",
        r"\bmongodb\s+schema\b",
        r"\bpostgres\s+(schema|design)\b",
        r"\bmaster\s+data\b",
        r"\bdata\s+governance\b",
    ]),

    # --- Process Design / PDC ---
    ("pdc", 0.85, [
        r"\bprocess\s+(design|flow|model)\b",
        r"\bworkflow\s+(design|orchestration)\b",
        r"\bbpmn\b",
        r"\bstate\s+machine\b",
        r"\bbusiness\s+process\b",
        r"\bprocess\s+automation\b",
        r"\bbpm\b",
        r"\borchestration\s+(design|flow)\b",
        r"\bsaga\s+pattern\b",
    ]),

    # --- Business Design / BDC ---
    ("bdc", 0.85, [
        r"\bbusiness\s+(model|design|architecture)\b",
        r"\bbusiness\s+process\s+diagram\b",
        r"\bvalue\s+stream\b",
        r"\bcapability\s+map\b",
        r"\bstakeholder\s+(map|analysis)\b",
        r"\bbusiness\s+case\b.*design",
        r"\borganizational\s+design\b",
    ]),

    # --- Observability Design / ODC ---
    ("odc", 0.87, [
        r"\bobservabilit",
        r"\bmonitoring\s+design\b",
        r"\blogging\s+architecture\b",
        r"\btracing\s+(design|architecture)\b",
        r"\btelemetry\s+design\b",
        r"\bsiem\s+design\b",
        r"\balerting\s+(design|strategy)\b",
        r"\bdashboard\s+design\b",
        r"\bslo\b.*design",
        r"\bsre\s+(design|architecture)\b",
        r"\bopentelemetry\b",
    ]),

    # --- Infrastructure Design / IDC ---
    ("idc", 0.88, [
        r"\binfrastructure\s+(design|architecture|as\s+code)\b",
        r"\bterraform\s+design\b",
        r"\bk8s\s+design\b",
        r"\bkubernetes\s+(design|architecture)\b",
        r"\bcloud\s+(infrastructure|architecture)\b",
        r"\biac\b.*design",
        r"\bansible\b.*design",
        r"\bcontainer\s+(design|architecture)\b",
        r"\bmicroservices\s+architecture\b",
        r"\bservice\s+mesh\b",
        r"\bhelm\s+(chart|design)\b",
        r"\bcluster\s+design\b",
        r"\bgitops\b",
        r"\bdeployment\s+architecture\b",
    ]),
]

# Intake signals — if these match strongly, stay in intake regardless
_INTAKE_STRONG: list[str] = [
    r"\bi\s+(need|want|would\s+like)\s+(to\s+build|to\s+create|an?\s+app)",
    r"\bbuild\s+(me\s+)?a\b",
    r"\bcreate\s+(a|an)\s+\w+\s+(app|application|system|tool)\b",
    r"\bnew\s+(project|app|application|system)\b",
    r"\brequirement",
    r"\bspe[ck]\b.*\bfor\b",
    r"\buser\s+stor",
    r"\bacceptance\s+criteria\b",
    r"\bricoas\b",
    r"\bproject\s+brief\b",
    r"\bfeasibilit",
    # Intelligence / OSINT / ISR — always requirements intake, never NDC
    r"\bosint\b",
    r"\bopen.source\s+intelligence\b",
    r"\bintelligence\s+(analysis|gather|collect|monitor|fusion|report)\b",
    r"\bisr\b",  # Intelligence, Surveillance, Reconnaissance
    r"\bsigint\b",
    r"\bgeoint\b",
    r"\bhumint\b",
    r"\bmasint\b",
    r"\bfinint\b",
    r"\bthreat\s+(intelligence|actor|hunt)\b",
    r"\bindicator\s+of\s+(compromise|attack)\b",
    r"\bioc\b",
    r"\bconflict\s+(monitor|track|analys)",
    r"\battribution\b",
    r"\badversary\s+(track|profile|analys)\b",
    r"\bsituation\s+(aware|report|monitor)\b",
    r"\btargeting\s+(analys|system)\b",
]


def _score_message(text: str) -> dict[str, Any]:
    """Score a message against keyword rules. Returns best match or intake."""
    lower = text.lower()

    # Check intake signals first
    for pat in _INTAKE_STRONG:
        if re.search(pat, lower):
            return {"mode": INTAKE_MODE, "canvas_type": None, "confidence": 0.90, "reason": "intake keyword matched"}

    best_canvas: str | None = None
    best_score: float = 0.0
    best_count = 0

    for canvas_type, weight, patterns in _RULES:
        hits = sum(1 for p in patterns if re.search(p, lower))
        if hits == 0:
            continue
        # Score: weight * sqrt(hits) — more hits = higher confidence, diminishing returns
        import math
        score = weight * math.sqrt(hits)
        if score > best_score:
            best_score = score
            best_canvas = canvas_type
            best_count = hits

    if best_canvas and best_score >= 0.70:
        # Normalise to 0–1: cap at 1.0
        confidence = min(best_score / 1.2, 1.0)
        return {
            "mode": best_canvas,
            "canvas_type": best_canvas,
            "confidence": round(confidence, 3),
            "reason": f"keyword match: {best_count} rule(s) hit for {best_canvas.upper()}",
        }

    return {"mode": INTAKE_MODE, "canvas_type": None, "confidence": 0.55, "reason": "no strong keyword signal"}


def _llm_classify(text: str) -> dict[str, Any]:
    """LLM fallback for ambiguous messages. Returns same shape as _score_message."""
    try:
        from tools.llm.router import LLMRouter

        router = LLMRouter()
        provider, model_id, _ = router.get_provider_for_function("classification")

        prompt = (
            f"Classify the following user message into one of these chat modes:\n"
            f"- intake: user wants to build a new app / capture requirements / describe a project\n"
            f"- cam: migration, modernization, deprecated tech, refactoring, EOL\n"
            f"- ndc: network topology, firewall, VLAN, routing, network design\n"
            f"- sdc: security architecture, threat model, IAM design, zero trust\n"
            f"- eda: data pipeline, event streaming, ETL, Kafka, data architecture\n"
            f"- ddc: database schema, ER diagram, data model, SQL/NoSQL design\n"
            f"- pdc: process flow, workflow, BPMN, state machine\n"
            f"- bdc: business model, capability map, value stream\n"
            f"- odc: observability, monitoring, logging, tracing design\n"
            f"- idc: infrastructure, Terraform, Kubernetes, cloud architecture\n\n"
            f"Message: {text[:500]}\n\n"
            f"Reply with ONLY a JSON object: {{\"mode\": \"<mode>\", \"reason\": \"<one sentence>\"}}"
        )

        response = provider.complete(model_id=model_id, prompt=prompt, max_tokens=100)
        raw = (response or "").strip()

        import json
        # Extract JSON from possible markdown fences
        json_match = re.search(r"\{[^}]+\}", raw)
        if json_match:
            obj = json.loads(json_match.group())
            mode = obj.get("mode", INTAKE_MODE)
            if mode not in CANVAS_MODES and mode != INTAKE_MODE:
                mode = INTAKE_MODE
            return {
                "mode": mode,
                "canvas_type": mode if mode in CANVAS_MODES else None,
                "confidence": 0.78,
                "reason": f"LLM: {obj.get('reason', '')}",
            }
    except Exception:
        pass

    return {"mode": INTAKE_MODE, "canvas_type": None, "confidence": 0.50, "reason": "LLM unavailable, defaulting to intake"}


def classify(message: str) -> dict[str, Any]:
    """Classify a user message. Returns mode, canvas_type, confidence, reason."""
    if not message or not message.strip():
        return {"mode": INTAKE_MODE, "canvas_type": None, "confidence": 1.0, "reason": "empty message"}

    result = _score_message(message)

    # Use LLM for low-confidence keyword results (not for clear intake signals)
    if result["confidence"] < 0.70 and result["mode"] == INTAKE_MODE:
        result = _llm_classify(message)

    return result
