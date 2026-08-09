#!/usr/bin/env python3
# CUI // SP-CTI
"""GovCon Sanitizer — pre-LLM hook for proposal content.

The critical integration point: wraps proposal content before it's sent to
any LLM (cloud or local). Applies PII detection + anonymization + past
performance generalization.

Integration points:
    - tools/govcon/response_drafter.py:_try_llm_draft()  → sanitize prompt
    - tools/proposal_genesis/reflexes/draft.py            → sanitize context
    - tools/proposal_genesis/reflexes/publish.py          → sanitize case study

Features:
    - Detects and anonymizes standard PII (names, SSN, email, phone)
    - Detects and anonymizes GovCon-specific data (contract#, CAGE, pricing)
    - Replaces deny-listed program names and organizations with codenames
    - Generalizes past performance indicators (dates, amounts, counts)
    - Session-scoped consistency (same entity → same surrogate)
    - De-anonymizes LLM output for human consumption
    - Skips redaction for local-only LLM routing (configurable)

CLI:
    python tools/redaction/govcon_sanitizer.py --sanitize "proposal text" --json
    python tools/redaction/govcon_sanitizer.py --sanitize "text" --show-text --json
    python tools/redaction/govcon_sanitizer.py --health --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.redaction.anonymizer import RedactionAnonymizer  # noqa: E402

logger = get_logger("icdev.redaction.govcon_sanitizer")


def _load_govcon_config() -> Dict[str, Any]:
    config_path = BASE_DIR / "args" / "redaction_govcon.yaml"
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _load_redaction_config() -> Dict[str, Any]:
    config_path = BASE_DIR / "args" / "redaction_config.yaml"
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Past Performance Generalizer
# ---------------------------------------------------------------------------


class PastPerformanceGeneralizer:
    """Generalizes identifiable details in past performance narratives."""

    def __init__(self, config: Dict[str, Any]):
        pp_cfg = config.get("past_performance", {})
        self.generalize_amounts = pp_cfg.get("generalize_amounts", True)
        self.generalize_dates = pp_cfg.get("generalize_dates", True)
        self.generalize_counts = pp_cfg.get("generalize_counts", True)
        self.count_rounding = pp_cfg.get("count_rounding", 10)
        self.trigger_phrases = [p.lower() for p in pp_cfg.get("trigger_phrases", [])]

    def is_past_performance_context(self, text: str) -> bool:
        """Check if text appears to be past performance content."""
        text_lower = text.lower()
        return any(phrase in text_lower for phrase in self.trigger_phrases)

    def generalize(self, text: str) -> str:
        """Apply generalization rules to past performance text."""
        result = text

        if self.generalize_amounts:
            result = self._generalize_amounts(result)

        if self.generalize_dates:
            result = self._generalize_dates(result)

        if self.generalize_counts:
            result = self._generalize_counts(result)

        return result

    def _generalize_amounts(self, text: str) -> str:
        """Replace specific dollar amounts with ranges."""

        def _round_amount(match):
            full = match.group(0)
            # Extract numeric value
            num_str = re.sub(r"[,$]", "", match.group(1))
            suffix = match.group(2) or ""
            try:
                num = float(num_str)
                if suffix.upper() in ("M", "MILLION"):
                    if num < 5:
                        return "a multi-million dollar"
                    elif num < 50:
                        return "a tens-of-millions dollar"
                    else:
                        return "a large-scale"
                elif suffix.upper() in ("B", "BILLION"):
                    return "a billion-dollar"
                elif suffix.upper() in ("K",):
                    return f"approximately ${int(round(num / 10) * 10)}K"
                else:
                    if num < 1000:
                        return full  # Keep small amounts
                    elif num < 1000000:
                        return f"approximately ${int(round(num / 1000) * 1000):,}"
                    else:
                        return "a multi-million dollar"
            except (ValueError, TypeError):
                return full

        result = re.sub(r"\$([0-9,]+(?:\.\d{1,2})?)\s?([MBKmk](?:illion)?)?", _round_amount, text)
        return result

    def _generalize_dates(self, text: str) -> str:
        """Replace specific dates with quarters/ranges."""
        months = {
            "january": "Q1",
            "february": "Q1",
            "march": "Q1",
            "april": "Q2",
            "may": "Q2",
            "june": "Q2",
            "july": "Q3",
            "august": "Q3",
            "september": "Q3",
            "october": "Q4",
            "november": "Q4",
            "december": "Q4",
        }

        def _replace_month_year(match):
            month = match.group(1).lower()
            year = match.group(2)
            quarter = months.get(month, "")
            if quarter:
                return f"{quarter} {year}"
            return match.group(0)

        result = re.sub(
            r"\b(January|February|March|April|May|June|July|August|"
            r"September|October|November|December)\s+(\d{4})\b",
            _replace_month_year,
            text,
            flags=re.IGNORECASE,
        )
        return result

    def _generalize_counts(self, text: str) -> str:
        """Replace specific personnel/team counts with rounded values."""

        def _round_count(match):
            prefix = match.group(1)
            num = int(match.group(2))
            suffix = match.group(3)
            if num < 5:
                return match.group(0)  # Keep small counts exact
            rounding = self.count_rounding
            rounded = int(round(num / rounding) * rounding)
            if rounded == 0:
                rounded = rounding
            return f"{prefix}{rounded}+{suffix}"

        # "team of 47", "47 engineers", "staff of 123"
        result = re.sub(
            r"(team of |staff of |group of )?(\d{2,})(\s+(?:engineers|developers|"
            r"staff|people|members|personnel|specialists|analysts|contractors))",
            _round_count,
            text,
            flags=re.IGNORECASE,
        )
        return result


# ---------------------------------------------------------------------------
# GovCon Sanitizer
# ---------------------------------------------------------------------------


class GovConSanitizer:
    """Pre-LLM sanitizer for government proposal content."""

    def __init__(self, session_id: Optional[str] = None):
        self._config = _load_redaction_config()
        self._govcon_config = _load_govcon_config()
        self._anonymizer = RedactionAnonymizer(
            config=self._config,
            govcon_config=self._govcon_config,
            session_id=session_id,
        )
        self._pp_generalizer = PastPerformanceGeneralizer(self._govcon_config)
        self._skip_local = self._config.get("scope", {}).get("skip_for_local_only", True)

    @property
    def session_id(self) -> str:
        return self._anonymizer.session_id

    def sanitize_for_llm(
        self, text: str, function_name: str = "", impact_level: str = "IL4", is_local_only: bool = False
    ) -> Tuple[str, Dict[str, Any]]:
        """Sanitize text before sending to LLM.

        Args:
            text: Raw proposal content / prompt text.
            function_name: LLM function name (for scope checking).
            impact_level: Classification level.
            is_local_only: True if routing to local Ollama only.

        Returns:
            Tuple of (sanitized_text, metadata_dict).
        """
        # Check if module is enabled
        if not self._config.get("enabled", True):
            return text, {"skipped": True, "reason": "redaction_disabled"}

        scope = self._config.get("scope", {})
        mode = scope.get("mode", "all")
        exempt = scope.get("exempt_modules", [])
        enforced = scope.get("enforced_modules", [])

        # Check if this function is exempt
        if function_name in exempt:
            return text, {"skipped": True, "reason": "exempt_module"}

        # In "enforced_only" mode, skip if not in enforced list
        if mode == "enforced_only" and function_name and function_name not in enforced:
            return text, {"skipped": True, "reason": "not_in_enforced_list"}

        # Skip for local-only routing (if configured)
        # But NEVER skip for explicitly enforced modules
        if is_local_only and self._skip_local and function_name not in enforced:
            return text, {"skipped": True, "reason": "local_only_routing"}

        # 1. PII detection + anonymization
        result = self._anonymizer.anonymize(text, impact_level=impact_level)
        sanitized = result.anonymized_text

        # 2. Past performance generalization (if in PP context)
        pp_applied = False
        if self._pp_generalizer.is_past_performance_context(text):
            sanitized = self._pp_generalizer.generalize(sanitized)
            pp_applied = True

        metadata = {
            "skipped": False,
            "session_id": self.session_id,
            "detection_count": len(result.detections),
            "replacement_count": len(result.replacements),
            "entity_types": result.to_dict().get("entity_types", []),
            "past_performance_generalized": pp_applied,
            "impact_level": impact_level,
            "function_name": function_name,
        }

        return sanitized, metadata

    def de_anonymize_response(self, text: str) -> str:
        """De-anonymize LLM response text using session registry.

        Call this on the LLM output to restore real values before
        presenting to the user.

        Args:
            text: Anonymized LLM response.

        Returns:
            Text with surrogates replaced by real values.
        """
        return self._anonymizer.de_anonymize(text)

    def sanitize_case_study(self, title: str, body: str, tags: Optional[List[str]] = None) -> Dict[str, str]:
        """Sanitize a Pulse case study for publication.

        Applies stricter rules than LLM sanitization:
            - Strips agency names from tags
            - Generalizes agencies in body
            - Strips NAICS codes
            - Applies past performance generalization

        Args:
            title: Case study title.
            body: Case study markdown body.
            tags: List of tags.

        Returns:
            Dict with sanitized title, body, and tags.
        """
        pulse_cfg = self._govcon_config.get("pulse_sanitization", {})

        # Sanitize body through anonymizer
        result = self._anonymizer.anonymize(body, impact_level="IL4")
        sanitized_body = result.anonymized_text

        # Additional Pulse-specific sanitization
        if pulse_cfg.get("generalize_agencies", True):
            replacement = pulse_cfg.get("agency_replacement", "a federal agency")
            agency_map = self._govcon_config.get("agency_surrogates", {}) or {}
            for agency_name in agency_map.keys():
                sanitized_body = re.sub(re.escape(agency_name), replacement, sanitized_body, flags=re.IGNORECASE)

        if pulse_cfg.get("strip_naics", True):
            sanitized_body = re.sub(r"\*\*NAICS:\*\*\s*\d+\s*", "", sanitized_body)

        # Generalize past performance
        sanitized_body = self._pp_generalizer.generalize(sanitized_body)

        # Sanitize title
        sanitized_title = self._strip_solicitation(title)
        result_title = self._anonymizer.anonymize(sanitized_title, impact_level="IL4")
        sanitized_title = result_title.anonymized_text

        # Sanitize tags
        sanitized_tags = tags[:] if tags else []
        if pulse_cfg.get("strip_agency_from_tags", True):
            agency_names = set(a.lower() for a in (self._govcon_config.get("agency_surrogates", {}) or {}).keys())
            sanitized_tags = [
                t
                for t in sanitized_tags
                if t.lower() not in agency_names and not any(a in t.lower() for a in agency_names)
            ]
        if pulse_cfg.get("strip_naics", True):
            sanitized_tags = [t for t in sanitized_tags if not t.startswith("NAICS-")]

        return {
            "title": sanitized_title,
            "body": sanitized_body,
            "tags": sanitized_tags,
        }

    @staticmethod
    def _strip_solicitation(title: str) -> str:
        """Remove solicitation numbers from title."""
        title = re.sub(r"\b[A-Z0-9]{2,}-\d{2}-[A-Z]-\d+\b", "", title)
        title = re.sub(r"\s+", " ", title).strip()
        if len(title) > 120:
            title = title[:117] + "..."
        return title

    def health(self) -> Dict[str, Any]:
        """Health check."""
        anon_health = self._anonymizer.health()
        return {
            "status": "ok",
            "anonymizer": anon_health,
            "past_performance_triggers": len(self._pp_generalizer.trigger_phrases),
            "skip_for_local_only": self._skip_local,
            "enforced_modules": self._config.get("scope", {}).get("enforced_modules", []),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="ICDEV™ GovCon Sanitizer")
    parser.add_argument("--sanitize", type=str, help="Sanitize text for LLM")
    parser.add_argument("--sanitize-file", type=str, help="Sanitize file")
    parser.add_argument("--function", type=str, default="proposal_drafting", help="LLM function name")
    parser.add_argument("--il", type=str, default="IL4", choices=["IL2", "IL4", "IL5", "IL6"])
    parser.add_argument("--local-only", action="store_true", help="Simulate local-only routing")
    parser.add_argument("--show-text", action="store_true", help="Show sanitized text")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--gate", action="store_true")
    args = parser.parse_args()

    sanitizer = GovConSanitizer()

    if args.health:
        result = sanitizer.health()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Status: {result['status']}")
        if args.gate and result["status"] != "ok":
            sys.exit(1)
        return

    text = args.sanitize
    if args.sanitize_file:
        text = Path(args.sanitize_file).read_text(encoding="utf-8")

    if text:
        sanitized, metadata = sanitizer.sanitize_for_llm(
            text,
            function_name=args.function,
            impact_level=args.il,
            is_local_only=args.local_only,
        )
        output = metadata.copy()
        if args.show_text:
            output["sanitized_text"] = sanitized
        if args.json:
            print(json.dumps(output, indent=2))
        else:
            print(f"Detections: {metadata.get('detection_count', 0)}")
            print(f"Replacements: {metadata.get('replacement_count', 0)}")
            if args.show_text:
                print(f"\nSanitized:\n{sanitized}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
