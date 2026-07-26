# CUI // SP-CTI
"""Data Mesh Governance Engine — policy management, OPA-style access control, and scoring."""

import json
import uuid
from datetime import datetime, timezone

from tools.data_canvas.db.init_db import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


def _safe_int(value) -> int:
    """Best-effort int coercion for maturity levels (dcpr-fix-08).

    Legacy rows may hold a non-numeric label (e.g. the string 'defined') in the
    INTEGER maturity_level column. Treat any unparseable value as 0 (lowest)
    rather than raising ValueError and crashing the whole score.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def list_policies(domain_id: str | None = None) -> list:
    """List governance policies, optionally filtered by domain (applies_to)."""
    with get_connection() as conn:
        if domain_id:
            rows = conn.execute(
                "SELECT * FROM dm_governance_policies WHERE applies_to IN (?, 'all') AND status='active' ORDER BY name",
                (domain_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM dm_governance_policies ORDER BY name"
            ).fetchall()
    result = []
    for r in rows:
        row = dict(r)
        try:
            row["rules"] = json.loads(row.get("rules_json") or "[]")
        except Exception:
            row["rules"] = []
        result.append(row)
    return result


def create_policy(data: dict) -> dict:
    """Create a new governance policy in dm_governance_policies."""
    if not data:
        raise ValueError("data is required")
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    policy_id = f"pol-{_uid()[:12]}"
    now = _now()
    row = {
        "id": policy_id,
        "name": name,
        "policy_type": data.get("policy_type", "opa"),
        "rules_json": json.dumps(data.get("rules", [])),
        "applies_to": data.get("applies_to", "all"),
        "status": data.get("status", "active"),
        "classification": data.get("classification", "CUI // SP-CTI"),
        "created_at": now,
        "updated_at": now,
    }
    # Positional ? + ordered tuple: get_connection() is HYBRID (raw sqlite3 on the
    # SQLite branch — no translate wrapper; StorageConnection on PG). translate_sql
    # only rewrites ?→%s (never :name), and StorageCursor wraps a dict param into a
    # 1-tuple, so a :name mapping never reaches either driver. Column order MUST
    # match _DM_POLICY_COLS.
    _DM_POLICY_COLS = (
        "id", "name", "policy_type", "rules_json", "applies_to", "status",
        "classification", "created_at", "updated_at",
    )
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO dm_governance_policies
               (id, name, policy_type, rules_json, applies_to, status, classification, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            tuple(row[c] for c in _DM_POLICY_COLS),
        )
        conn.commit()
    return row


def _eval_rule(actual, op: str, expected) -> bool:
    """Evaluate a single OPA policy rule predicate."""
    if op in ("eq", "=="):
        return actual == expected
    if op in ("neq", "!="):
        return actual != expected
    if op == "in":
        return actual in (expected or [])
    if op == "not_in":
        return actual not in (expected or [])
    if op in ("gte", ">=") and actual is not None:
        try:
            return float(actual) >= float(expected)
        except (TypeError, ValueError):
            return False
    if op in ("lte", "<=") and actual is not None:
        try:
            return float(actual) <= float(expected)
        except (TypeError, ValueError):
            return False
    if op == "exists":
        return actual is not None
    return False  # unknown op — fail closed (default deny)


def _is_sensitive_resource(resource: dict) -> bool:
    """A resource is sensitive unless explicitly marked non-sensitive.

    Fail-closed: only resources explicitly classified PUBLIC/UNCLASSIFIED (or
    flagged sensitive=False) are treated as non-sensitive. Missing or unknown
    classification is treated as sensitive so access defaults to deny.
    """
    if resource.get("sensitive") is True:
        return True
    if resource.get("sensitive") is False:
        return False
    classification = str(resource.get("classification") or "").strip().upper()
    return classification not in ("PUBLIC", "UNCLASSIFIED")


def check_access(user_attrs: dict | None, resource: dict | None) -> dict:
    """OPA-style ABAC access check against active governance policies.

    Returns {"allowed": bool, "reason": str, "matched_policies": list}.

    Default-deny: when no policy matches a sensitive resource, access is denied
    (fail closed). Only explicitly non-sensitive (PUBLIC/UNCLASSIFIED) resources
    default to allow when no policy applies.
    """
    user_attrs = user_attrs or {}
    resource = resource or {}
    domain_id = resource.get("domain_id") or resource.get("domain")

    policies = list_policies(domain_id=domain_id)
    if not policies:
        if _is_sensitive_resource(resource):
            return {
                "allowed": False,
                "reason": "No matching policy — default deny",
                "matched_policies": [],
            }
        return {
            "allowed": True,
            "reason": "No applicable policies — non-sensitive resource, default allow",
            "matched_policies": [],
        }

    violations: list[str] = []
    matched: list[dict] = []
    for pol in policies:
        rules = pol.get("rules") or []
        for rule in rules:
            attr = rule.get("attr", "")
            op = rule.get("op", "eq")
            val = rule.get("value")
            actual = user_attrs.get(attr) if attr in user_attrs else resource.get(attr)
            allowed = _eval_rule(actual, op, val)
            matched.append({"policy": pol["name"], "attr": attr, "op": op, "allowed": allowed})
            if not allowed:
                violations.append(f"Policy '{pol['name']}': {attr} must be {op} {val!r}")

    if violations:
        return {"allowed": False, "reason": "; ".join(violations), "matched_policies": matched}
    return {"allowed": True, "reason": "All policies satisfied", "matched_policies": matched}


def compute_governance_score(domain_id: str | None = None) -> dict:
    """Compute governance score for one domain or all active domains.

    Scoring pillars (25 pts each):
    - data_products: domain has ≥1 active data product
    - policies: at least one active policy applies to domain
    - maturity: domain maturity_level ≥ 2
    - ownership: owner and steward both set

    Returns {"score": float, "grade": str, "domain_count": int, "policy_count": int, "domains": list}.
    """
    with get_connection() as conn:
        if domain_id:
            domain_rows = conn.execute(
                "SELECT * FROM dm_domains WHERE id=?", (domain_id,)
            ).fetchall()
        else:
            domain_rows = conn.execute(
                "SELECT * FROM dm_domains WHERE status='active' ORDER BY name"
            ).fetchall()

        policy_rows = conn.execute(
            "SELECT id, name, applies_to FROM dm_governance_policies WHERE status='active'"
        ).fetchall()
        product_rows = conn.execute(
            "SELECT domain_id FROM dm_data_products WHERE status='active'"
        ).fetchall()
        maturity_rows = conn.execute(
            "SELECT domain_id, MAX(maturity_level) AS max_level FROM dm_domain_maturity GROUP BY domain_id"
        ).fetchall()

    domains = [dict(r) for r in domain_rows]
    active_policies = [dict(r) for r in policy_rows]

    products_by_domain: dict[str, int] = {}
    for p in product_rows:
        did = p["domain_id"] or ""
        products_by_domain[did] = products_by_domain.get(did, 0) + 1

    maturity_by_domain: dict[str, int] = {r["domain_id"]: r["max_level"] for r in maturity_rows}

    if not domains:
        return {
            "score": 0.0, "grade": "N/A", "domain_count": 0,
            "policy_count": len(active_policies), "domains": [],
            "details": {"reason": "No active domains"},
        }

    domain_scores = []
    for d in domains:
        pts = 0
        did = d["id"]
        if products_by_domain.get(did, 0) > 0:
            pts += 25
        if any(p["applies_to"] in ("all", did) for p in active_policies):
            pts += 25
        if _safe_int(maturity_by_domain.get(did) or d.get("maturity_level", 0) or 0) >= 2:
            pts += 25
        if d.get("owner") and d.get("steward"):
            pts += 25
        domain_scores.append({"id": did, "name": d["name"], "score": pts})

    overall = sum(s["score"] for s in domain_scores) / len(domain_scores)
    if overall >= 90:
        grade = "A"
    elif overall >= 75:
        grade = "B"
    elif overall >= 60:
        grade = "C"
    elif overall >= 50:
        grade = "D"
    else:
        grade = "F"

    return {
        "score": round(overall, 1),
        "grade": grade,
        "domain_count": len(domains),
        "policy_count": len(active_policies),
        "domains": domain_scores,
        "details": {"scoring_pillars": ["data_products", "policies", "maturity", "ownership"]},
    }
