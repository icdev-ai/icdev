# CUI // SP-CTI
"""ACE Problem Classifier Lens — maps a problem description to a TeamManifest.

Three-phase Oracle pipeline:
  1. analyze()  — extract RICOAS signals + keyword scores + role catalog
  2. score()    — map signal clusters to OraclePredictions with confidence
  3. propose()  — convert predictions to TeamManifest; LLM-assists when
                  pattern confidence is insufficient (<0.5)

Fallback: if max confidence < 0.5 and LLM is unavailable, returns
    TeamManifest([RoleSlot("ai_developer", 1), RoleSlot("qa_manager", 1)]).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from icdev.tools.ace.role_loader import RoleLoader
from icdev.tools.logging.icdev_logger import get_logger
from tools.oracle.base_lens import BaseLens, OraclePrediction

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RoleSlot:
    """A single role assignment in a team manifest."""

    role_id: str
    count: int = 1
    priority: str = "medium"  # critical | high | medium | low


@dataclass
class TeamManifest:
    """Proposed team composition for a given problem."""

    slots: list[RoleSlot] = field(default_factory=list)


# ---------------------------------------------------------------------------
# RICOAS signal patterns (sourced from tools.chat.requirement_intake_hook)
# ---------------------------------------------------------------------------

_SIGNALS: dict[str, tuple[str, float]] = {
    # (regex_pattern, weight)
    "shall":           (r"\bshall\b", 0.90),
    # ── Document domains (test_intel_report_lifecycle.py) ────────────────
    # Weighted at 0.90, on a par with "shall": keyword_scores averages the
    # matched weights and propose() ignores any domain scoring under 0.5, so a
    # lower weight would classify the domain correctly and then still hand back
    # the fallback team.
    "intelligence_noun": (
        r"\b(?:INTSUM|intelligence summary|intelligence report|IPB|CAPCO"
        r"|ICD 710|EO 13526|portion marking|derivative classif\w*"
        r"|indicator[s]?|INTREP|collection plan)\b",
        0.90,
    ),
    "intelligence_verb": (
        r"\b(?:collect|collection|assess|assessment|corroborat\w+"
        r"|declassif\w+|classif(?:y|ied|ication))\b",
        0.85,
    ),
    "legal_noun": (
        r"\b(?:plaintiff|defendant|motion|IRAC|attorney[- ]client"
        r"|privilege|deposition|statute|case law|brief)\b",
        0.90,
    ),
    "medical_noun": (
        r"\b(?:patient|clinical note[s]?|SOAP|HIPAA|PHI|diagnos\w+"
        r"|treatment plan|chart review)\b",
        0.90,
    ),
    "financial_noun": (
        r"\b(?:10-K|10-Q|SEC filing|MNPI|EBITDA|investment memo"
        r"|balance sheet|earnings|valuation)\b",
        0.90,
    ),
    "corporate_noun": (
        r"\b(?:SWOT|Porter Five Forces|Porter's Five Forces|market share"
        r"|KPI[s]?|competitive analysis|benchmark[s]?)\b",
        0.90,
    ),
    "doc_analysis_noun": (
        r"\b(?:research memo|key findings|executive summary"
        r"|document review|analyz\w+ this document|white paper)\b",
        0.90,
    ),

    "must":            (r"\bmust\b", 0.90),
    "should":          (r"\bshould\b", 0.70),
    "needs_to":        (r"\bneeds? to\b", 0.70),
    "has_to":          (r"\bhas to\b", 0.65),
    "requirement":     (r"\brequirement[s]?\b", 0.75),
    "user_story":      (r"\buser stor(?:y|ies)\b", 0.70),
    "as_a_i_want":     (r"\bas a\b.{0,60}\bi want\b", 0.65),
    "i_want":          (r"\bi want\b", 0.55),
    "we_want":         (r"\bwe want\b", 0.55),
    "bdd":             (r"\bgiven\b.{0,120}\bwhen\b.{0,120}\bthen\b", 0.80),
    "capability":      (r"\bcapabilit(?:y|ies)\b", 0.65),
    "acceptance":      (r"\bacceptance criteri(?:a|on)\b", 0.75),
    "system_shall":    (r"\bthe system\b.{0,60}\b(?:shall|should|must|will|needs?)\b", 0.85),
    "feature_request": (r"\bfeature request\b", 0.70),
    "functional_req":  (r"\bfunctional requirement\b", 0.80),
    # domain: build / develop
    "build_verb":      (r"(?:^|\. )(?:create|build|develop|design|implement)\b", 0.60),
    "deploy_verb":     (r"(?:^|\. )(?:deploy|integrate|generate)\b", 0.60),
    # domain: monitor / alert
    "monitor_verb":    (
        r"(?:^|\. )(?:monitor|capture|track|detect|alert|display|visuali[sz]e|depict)\b",
        0.60,
    ),
    # domain: analytics
    "analytics_verb":  (
        r"(?:^|\. )(?:analyze|analyse|process|correlate|aggregate|ingest|expose)\b",
        0.60,
    ),
    # domain: compliance / security
    "compliance_verb": (
        r"(?:^|\. )(?:ensure|enforce|provide|enable|allow|establish|configure)\b",
        0.55,
    ),
    # domain: product management (govcon PM, OST, bid/no-bid, roadmap, PRD)
    "product_mgmt_verb": (
        r"(?:^|\. )(?:product manager|govcon|ost|opportunity solution tree|"
        r"pre.mortem|bid.no.bid|roadmap|prd|battlecard|sprint plan|cpars|okr)\b",
        0.75,
    ),
    "strategy_noun": (
        r"\b(?:strategy|strategic|opportunity analysis|market analysis|"
        r"stakeholder|persona|discovery|continuous discovery|sam\.gov|naics)\b",
        0.65,
    ),
    # domain: software craft (spec, TDD, doubt-driven, ADR, code review)
    "craft_verb": (
        r"\b(?:author spec|tdd|test.driven|doubt.driven|"
        r"code review|craftsperson|adr|anti.rationali[sz]|falsif"
        r"|software craft|spec authoring|spec review)\b",
        0.75,
    ),
    "craft_noun": (
        r"\b(?:specification|unit test|integration test|"
        r"refactor|pull request|software craftsmanship)\b",
        0.55,
    ),
    # interest / desire (weaker)
    "interest":        (r"\bi(?:'?m| am) interested in\b", 0.40),
    "looking_for":     (r"\b(?:looking for|looking to|hoping to|plan to|trying to)\b", 0.40),
    "would_like":      (r"\bwould like\b", 0.40),
    "id_like":         (r"\bi(?:'?d| would) like\b", 0.40),
}

_COMPILED: dict[str, tuple[re.Pattern, float]] = {
    name: (re.compile(pat, re.IGNORECASE | re.DOTALL), weight)
    for name, (pat, weight) in _SIGNALS.items()
}

# Domain clusters: signal names -> role hints
_DOMAIN_SIGNALS: dict[str, list[str]] = {
    # ── Document domains ─────────────────────────────────────────────────
    # Each maps to the same five-role documentation team below: the work is
    # read-analyse-write-review-classify regardless of subject matter, which is
    # why one team serves all of them.
    "intelligence":   ["intelligence_noun", "intelligence_verb"],
    "legal":          ["legal_noun"],
    "medical":        ["medical_noun"],
    "financial":      ["financial_noun"],
    "corporate":      ["corporate_noun"],
    "doc_analysis":   ["doc_analysis_noun"],
    "requirements": [
        "shall", "must", "should", "needs_to", "has_to", "requirement",
        "user_story", "bdd", "acceptance", "functional_req", "system_shall",
        "as_a_i_want", "capability",
    ],
    "build":          ["build_verb", "feature_request"],
    "devops":         ["deploy_verb"],
    "monitoring":     ["monitor_verb"],
    "analytics":      ["analytics_verb"],
    "compliance":     ["compliance_verb"],
    "product_mgmt":   ["product_mgmt_verb", "strategy_noun"],
    "software_craft": ["craft_verb", "craft_noun"],
    "interest":       ["i_want", "we_want", "interest", "looking_for", "would_like", "id_like"],
}

# Domain -> preferred role_ids (matched against loaded catalog first)
#: The documentation team every document domain routes to.
_DOC_TEAM: tuple[str, ...] = (
    "researcher",
    "intelligence_analyst",
    "writer",
    "editor",
    "derivative_classifier",
)

_DOMAIN_ROLES: dict[str, list[str]] = {
    # The five-role documentation team. Read-analyse-write-review-classify
    # is the same pipeline whatever the subject matter, so every document
    # domain routes here rather than each growing its own near-duplicate.
    "intelligence":   list(_DOC_TEAM),
    "legal":          list(_DOC_TEAM),
    "medical":        list(_DOC_TEAM),
    "financial":      list(_DOC_TEAM),
    "corporate":      list(_DOC_TEAM),
    "doc_analysis":   list(_DOC_TEAM),
    "requirements":   ["requirements_engineer", "business_analyst", "product_owner"],
    "build":          ["ai_developer", "software_engineer", "developer"],
    "devops":         ["devops_engineer", "infrastructure_manager", "platform_engineer"],
    "monitoring":     ["system_monitor", "devops_engineer", "qa_manager"],
    "analytics":      ["data_analyst", "ai_developer", "analyst"],
    "compliance":     ["compliance_manager", "security_analyst", "compliance_officer"],
    "product_mgmt":   ["product_manager", "business_analyst", "requirements_engineer"],
    "software_craft": ["software_craftsperson", "ai_developer", "qa_manager"],
    "interest":       [],  # too weak to assign roles directly
}

_FALLBACK_SLOTS: list[RoleSlot] = [
    RoleSlot(role_id="ai_developer", count=1, priority="high"),
    RoleSlot(role_id="qa_manager", count=1, priority="medium"),
]


# ---------------------------------------------------------------------------
# Lens
# ---------------------------------------------------------------------------


class ProblemClassifierLens(BaseLens):
    """Oracle lens that classifies a problem description into a TeamManifest.

    Accepts problem_text at construction. The three-phase pipeline:
        analysis  = lens.analyze()
        preds     = lens.score(analysis)
        manifest  = lens.propose(preds)   # TeamManifest

    Note: propose() returns TeamManifest, not list[OraclePrediction], so
    BaseLens.run() is overridden to also return TeamManifest.
    """

    name: str = "problem_classifier"
    description: str = (
        "Classifies problem text into RICOAS signals and proposes a TeamManifest"
    )

    def __init__(
        self,
        problem_text: str,
        role_loader: RoleLoader | None = None,
    ) -> None:
        self._problem_text = problem_text
        self._role_loader = role_loader or RoleLoader()

    # ------------------------------------------------------------------
    # Phase 1: analyze
    # ------------------------------------------------------------------

    def analyze(self, problem_text: str | None = None) -> dict[str, Any]:
        """Extract RICOAS signals, keyword scores, and role catalog.

        Args:
            problem_text: Optional override; falls back to value set at construction.

        Returns:
            dict with keys: problem_text, ricoas_signals, keyword_scores, role_catalog.
        """
        text = problem_text if problem_text is not None else self._problem_text

        # Match every signal pattern
        ricoas_signals: dict[str, dict] = {
            name: {"matched": bool(pattern.search(text)), "weight": weight}
            for name, (pattern, weight) in _COMPILED.items()
        }

        # Domain score = average weight of *matched* signals in that domain
        # (unmatched signals don't dilute the score)
        keyword_scores: dict[str, float] = {}
        for domain, signal_names in _DOMAIN_SIGNALS.items():
            matched = [
                ricoas_signals[s]["weight"]
                for s in signal_names
                if ricoas_signals.get(s, {}).get("matched", False)
            ]
            keyword_scores[domain] = (
                min(1.0, sum(matched) / len(matched)) if matched else 0.0
            )

        # Role catalog snapshot
        try:
            roles = self._role_loader.list_roles()
        except Exception:
            roles = []

        role_catalog = [
            {
                "role_id": r.role_id,
                "display_name": r.display_name,
                "description": r.description,
                "llm_function": r.llm_function,
            }
            for r in roles
        ]

        return {
            "problem_text": text,
            "ricoas_signals": ricoas_signals,
            "keyword_scores": keyword_scores,
            "role_catalog": role_catalog,
        }

    # ------------------------------------------------------------------
    # Phase 2: score
    # ------------------------------------------------------------------

    def score(self, analysis: dict[str, Any]) -> list[OraclePrediction]:
        """Map domain keyword scores to OraclePredictions ranked by confidence.

        Returns:
            list[OraclePrediction] sorted descending by confidence.
        """
        keyword_scores: dict[str, float] = analysis.get("keyword_scores", {})
        role_catalog: list[dict] = analysis.get("role_catalog", [])
        loaded_ids = {r["role_id"] for r in role_catalog}

        predictions: list[OraclePrediction] = []

        for domain, preferred in _DOMAIN_ROLES.items():
            domain_score = keyword_scores.get(domain, 0.0)
            if domain_score < 0.05:
                continue

            # Prefer roles already in the loaded catalog; fall back to hint list
            candidates = [r for r in preferred if r in loaded_ids] or preferred
            if not candidates:
                continue

            severity: str
            if domain_score >= 0.7:
                severity = "critical"
            elif domain_score >= 0.4:
                severity = "warning"
            else:
                severity = "info"

            # The domain's declared team, not an arbitrary slice of it. This
            # was `candidates[:3]`, which was a silent no-op while every domain
            # happened to declare exactly three roles — and then truncated the
            # five-role documentation team to three the moment one did not, so
            # the domain classified correctly and still returned a partial team.
            # Team size belongs to _DOMAIN_ROLES.
            top = candidates
            predictions.append(
                OraclePrediction(
                    lens=self.name,
                    title=f"{domain.title()} domain detected",
                    description=(
                        f"Signal strength {domain_score:.0%} for '{domain}'. "
                        f"Suggested roles: {', '.join(top)}."
                    ),
                    confidence=domain_score,
                    severity=severity,
                    category=domain,
                    recommendations=[f"Assign role: {r}" for r in top],
                    data={"domain": domain, "score": domain_score, "preferred_roles": top},
                )
            )

        predictions.sort(key=lambda p: p.confidence, reverse=True)
        return predictions

    # ------------------------------------------------------------------
    # Phase 3: propose
    # ------------------------------------------------------------------

    def propose(self, predictions: list[OraclePrediction]) -> TeamManifest:  # type: ignore[override]
        """Convert ranked predictions to a TeamManifest.

        When max confidence < 0.5, calls LLMRouter.invoke('task_decomposition')
        for role suggestions. Falls back to [ai_developer, qa_manager] if LLM
        is also unavailable.

        Args:
            predictions: Sorted list produced by score().

        Returns:
            TeamManifest with deduplicated RoleSlots.
        """
        max_confidence = max((p.confidence for p in predictions), default=0.0)

        if max_confidence < 0.5:
            llm_slots = self._llm_suggest_roles()
            return TeamManifest(slots=llm_slots or list(_FALLBACK_SLOTS))

        seen: dict[str, RoleSlot] = {}
        for pred in predictions:
            if pred.confidence < 0.1:
                continue
            priority = "high" if pred.confidence >= 0.6 else "medium"
            for role_id in pred.data.get("preferred_roles", []):
                if role_id not in seen:
                    seen[role_id] = RoleSlot(role_id=role_id, count=1, priority=priority)

        return TeamManifest(slots=list(seen.values()) or list(_FALLBACK_SLOTS))

    # ------------------------------------------------------------------
    # run() override — returns TeamManifest instead of list[OraclePrediction]
    # ------------------------------------------------------------------

    def run(self) -> TeamManifest:  # type: ignore[override]
        """Full pipeline: analyze → score → propose. Returns TeamManifest."""
        analysis = self.analyze()
        predictions = self.score(analysis)
        return self.propose(predictions)

    # ------------------------------------------------------------------
    # LLM helper
    # ------------------------------------------------------------------

    def _resolve_suggested_roles(self, raw_ids: list[str]) -> list[RoleSlot]:
        """Map model-named role ids onto roles that actually exist on disk.

        The model can only emit *strings*. Any name it invents that is not a
        role YAML becomes a ghost co-worker: ``team_assembler._build_specs``
        catches ``RoleNotFoundError``, builds a spec with ``llm_function=""``
        and no permissions, and ``CoWorkerThread`` then fails it immediately
        with ``role_not_found``. So an unmatched suggestion is strictly worse
        than no suggestion.

        Each id is therefore either matched to a real role (exactly, or via the
        same similarity search ``sme_registry`` uses for duplicate suppression)
        or dropped. Genuinely novel domains are handled upstream by the
        suggestion builder calling ``ensure_sme`` — never here, because this
        runs inside the controller's background thread where a synchronous
        generation would stall assembly with nobody to approve it.
        """
        if not raw_ids:
            return []

        # list_roles() returns RoleTemplate objects, not ids.
        try:
            known = {r.role_id for r in self._role_loader.list_roles()}
        except Exception as exc:  # noqa: BLE001 — degrade to no suggestions
            logger.warning("problem_classifier: role catalog unavailable: %s", exc)
            return []

        resolved: list[str] = []
        for raw in raw_ids:
            if raw in known:
                resolved.append(raw)
                continue
            match = self._nearest_known_role(raw, known)
            if match:
                logger.info(
                    "problem_classifier: mapped suggested role %r -> %r", raw, match
                )
                resolved.append(match)
            else:
                logger.info(
                    "problem_classifier: dropping unknown suggested role %r "
                    "(no role YAML; would fail as role_not_found)", raw
                )

        slots: list[RoleSlot] = []
        for i, role_id in enumerate(dict.fromkeys(resolved)):
            slots.append(
                RoleSlot(
                    role_id=role_id,
                    count=1,
                    priority="high" if i == 0 else "medium",
                )
            )
        return slots

    @staticmethod
    def _nearest_known_role(raw: str, known: set[str]) -> str:
        """Best existing role for *raw*, or "" if nothing is close enough."""
        try:
            from icdev.tools.ace.sme_registry import REUSE_THRESHOLD, _similarity
        except Exception:  # noqa: BLE001
            return ""

        label = raw.replace("_", " ")
        best, best_score = "", 0.0
        for candidate in known:
            score = _similarity(label, candidate.replace("_", " "))
            if score > best_score:
                best, best_score = candidate, score
        return best if best_score >= REUSE_THRESHOLD else ""

    def _llm_suggest_roles(self) -> list[RoleSlot]:
        """Call LLMRouter for role suggestions when pattern confidence is low.

        Returns empty list if LLM is unavailable so the caller uses the fallback.
        """
        try:
            from icdev.tools.llm.router import LLMRouter
            from icdev.tools.llm.provider import LLMRequest
        except ImportError:
            return []

        try:
            router = LLMRouter()
            request = LLMRequest(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Given the following problem description, list the top 3 most "
                            "appropriate AI team roles using snake_case IDs (e.g. ai_developer, "
                            "qa_manager, devops_engineer, compliance_manager, data_analyst). "
                            "Return ONLY a JSON array of strings.\n\n"
                            f"Problem: {self._problem_text}"
                        ),
                    }
                ],
                system_prompt=(
                    "You are an AI team composition advisor. "
                    "Return only valid JSON — a JSON array of role_id strings."
                ),
                max_tokens=256,
                temperature=0.2,
                skip_injection_scan=True,
            )
            response = router.invoke("task_decomposition", request)
            content = response.content.strip()
            start = content.find("[")
            end = content.rfind("]") + 1
            if start >= 0 and end > start:
                role_ids: list = json.loads(content[start:end])
                raw = [str(r) for r in role_ids[:3] if isinstance(r, str) and r]
                return self._resolve_suggested_roles(raw)
        except Exception:  # noqa: BLE001 — LLMUnavailableError, network, parse errors
            pass
        return []
