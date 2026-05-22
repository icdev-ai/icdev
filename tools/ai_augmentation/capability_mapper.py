# CUI // SP-CTI
"""AI Augmentation Canvas (AAC) — Capability Mapper.

Maps detected opportunities to NIST AI RMF functions and OWASP LLM Top 10
risk tags for governance alignment.

Public API:
    map_capabilities(opportunity, scores, scan_context) -> dict

Usage:
    from tools.ai_augmentation.capability_mapper import map_capabilities
    tags = map_capabilities(opp, scores, {"il_level": "il5"})
    # tags == {
    #     "nist_ai_rmf": {
    #         "functions": ["Map", "Govern"],
    #         "rationale": "autonomous decision agent at IL5"
    #     },
    #     "owasp_llm_risk": "LLM06"
    # }
"""
from __future__ import annotations

# ── OWASP keyword heuristics ────────────────────────────────────────────────

_CRYPTO_KEYWORDS: frozenset[str] = frozenset({
    "encrypt", "decrypt", "cipher", "crypto", "cryptographic",
    "hash", "sha", "md5", "aes", "rsa", "pgp", "gpg",
    "hmac", "signature", "sign", "verify", "checksum",
    "nonce", "salt", "bcrypt", "scrypt", "argon",
    "cert", "certificate", "ssl", "tls",
    "secret", "vault", "key", "keys",
})

_AUTH_KEYWORDS: frozenset[str] = frozenset({
    "auth", "authn", "authz", "authentication", "authorization",
    "login", "logout", "signin", "signout", "signup", "register",
    "oauth", "sso", "ldap", "saml", "oidc",
    " rbac", "abac", "acl", "permission", "role", "roles",
    "session", "cookie", "token", "jwt", "credential", "credentials",
    "access_control", "accesscontrol", "gatekeeper",
    "password", "passcode", "pin", "mfa", "2fa", "totp",
})


def _check_keywords(text: str, keywords: frozenset[str]) -> bool:
    """Case-insensitive keyword search in text."""
    lowered = text.lower()
    for kw in keywords:
        if kw in lowered:
            return True
    return False


def _detect_owasp_risk(opportunity: dict) -> str | None:
    """Detect OWASP LLM Top 10 risk tag based on opportunity metadata.

    Returns:
        'LLM01' if auth-related keywords are found (prompt-injection / auth-bypass risk).
        'LLM06' if crypto-related keywords are found (sensitive-info-disclosure risk).
        None  if neither is detected.
        When both are present, LLM06 (disclosure) takes precedence.
    """
    module_path = opportunity.get("module_path", "")
    function_name = opportunity.get("function_name", "")
    pattern_detail = opportunity.get("pattern_detail", {})

    # Flatten pattern_detail values to strings for keyword scanning.
    detail_text = ""
    if isinstance(pattern_detail, dict):
        for v in pattern_detail.values():
            if isinstance(v, str):
                detail_text += " " + v
            elif isinstance(v, (list, tuple, set, frozenset)):
                for item in v:
                    if isinstance(item, str):
                        detail_text += " " + item

    combined = f"{module_path} {function_name} {detail_text}"

    crypto_hit = _check_keywords(combined, _CRYPTO_KEYWORDS)
    auth_hit = _check_keywords(combined, _AUTH_KEYWORDS)

    if crypto_hit:
        return "LLM06"
    if auth_hit:
        return "LLM01"
    return None


def _build_rationale(functions: list[str], ai_paradigm: str, il_level: str) -> str:
    """Build a concise human-readable rationale string."""
    parts: list[str] = []
    if "Govern" in functions:
        parts.append(f"autonomous {ai_paradigm.replace('_', ' ')}")
    if "Measure" in functions:
        parts.append("high-feasibility opportunity")
    if "Manage" in functions:
        parts.append("elevated risk profile")
    if il_level in ("il5", "il6"):
        parts.append(f"at {il_level.upper()}")

    if parts:
        return " ".join(parts)
    return "standard AI impact mapping"


# ── Public API ────────────────────────────────────────────────────────────────


def map_capabilities(opportunity: dict, scores: dict, scan_context: dict) -> dict:
    """Map an opportunity to NIST AI RMF functions and OWASP LLM risk tags.

    Mapping rules (NIST AI RMF functions):
      • All opportunities -> Map
      • feasibility_score > 0.7 -> Measure
      • ai_paradigm in [agentic_trigger, decision_agent] -> Govern
      • risk_score > 0.6 -> Manage
      • il_level in [il5, il6] -> Govern

    OWASP LLM Top 10:
      • LLM01 — if module_path / function_name / pattern_detail touches auth keywords.
      • LLM06 — if module_path / function_name / pattern_detail touches crypto keywords.
      • None  — otherwise.

    Args:
        opportunity:  Opportunity dict with at least keys:
                        pattern_type, ai_paradigm, module_path, function_name,
                        pattern_detail (dict).
        scores:       Score dict with at least keys:
                        feasibility_score, risk_score.
        scan_context: Dict with at least key:
                        il_level ("il4" | "il5" | "il6").

    Returns:
        Dict with keys:
            nist_ai_rmf: dict {functions: list[str], rationale: str}
            owasp_llm_risk: str | None
    """
    ai_paradigm = opportunity.get("ai_paradigm", "")
    il_level = str(scan_context.get("il_level", "il4")).lower()

    feasibility_score = float(scores.get("feasibility_score", 0.0))
    risk_score = float(scores.get("risk_score", 0.0))

    functions: set[str] = {"Map"}

    if feasibility_score > 0.7:
        functions.add("Measure")

    if ai_paradigm in ("agentic_trigger", "decision_agent"):
        functions.add("Govern")

    if risk_score > 0.6:
        functions.add("Manage")

    if il_level in ("il5", "il6"):
        functions.add("Govern")

    # Deterministic order: Map, Measure, Manage, Govern
    ordered_functions = [f for f in ("Map", "Measure", "Manage", "Govern") if f in functions]

    rationale = _build_rationale(ordered_functions, ai_paradigm, il_level)

    owasp_risk = _detect_owasp_risk(opportunity)

    return {
        "nist_ai_rmf": {
            "functions": ordered_functions,
            "rationale": rationale,
        },
        "owasp_llm_risk": owasp_risk,
    }
