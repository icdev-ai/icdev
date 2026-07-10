# CUI // SP-CTI
"""Crypto & protocol currency pack — pure rulebook, zero external deps.

Deterministic evidence source: args/docmod/rulebook_crypto.yaml. Every verdict
and replacement comes from a rule entry; findings cite the rule id
(``rule:<id>``). No LLM anywhere in this module (TRUST rule 1).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from tools.logging.icdev_logger import get_logger

from ..base_pack import CandidateEntity, ChunkRef, DomainPack, Replacement, Verdict

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
RULEBOOK_PATH = _REPO_ROOT / "args" / "docmod" / "rulebook_crypto.yaml"

_cache: dict = {"mtime": None, "rules": None}


def load_rulebook(path: Path | None = None) -> list[dict]:
    """Rules with compiled patterns, mtime hot-reloaded."""
    import yaml

    path = path or RULEBOOK_PATH
    if not path.exists():
        return []
    mtime = path.stat().st_mtime
    if _cache["rules"] is not None and _cache["mtime"] == mtime and path == RULEBOOK_PATH:
        return _cache["rules"]
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules = []
    for r in raw.get("rules", []):
        try:
            rules.append({**r, "compiled": re.compile(r["pattern"])})
        except (KeyError, re.error) as exc:
            logger.warning("docmod crypto rulebook: bad rule %s: %s", r.get("id"), exc)
    if path == RULEBOOK_PATH:
        _cache["rules"], _cache["mtime"] = rules, mtime
    return rules


class CryptoProtocolsPack(DomainPack):
    pack_id = "crypto_protocols"
    entity_types = ["protocol", "crypto_algorithm"]

    def _rules(self) -> list[dict]:
        return load_rulebook()

    def extract(self, text: str, chunk_ref: ChunkRef) -> list[CandidateEntity]:
        out: list[CandidateEntity] = []
        seen: set[str] = set()
        for rule in self._rules():
            for m in rule["compiled"].finditer(text):
                label = m.group(0).strip()
                key = f"{rule['id']}|{label.lower()}"
                if key in seen:
                    continue
                seen.add(key)
                start = max(0, m.start() - 60)
                out.append(CandidateEntity(
                    label=label,
                    entity_type=rule.get("entity_type", "protocol"),
                    pack_id=self.pack_id,
                    chunk_ref=chunk_ref,
                    raw_match=label,
                    context=text[start:m.end() + 60].strip(),
                    attributes={"rule_id": rule["id"]},
                ))
        return out

    def evaluate(self, entity: CandidateEntity, conn) -> Verdict:
        rule = next(
            (r for r in self._rules() if r["id"] == entity.attributes.get("rule_id")),
            None,
        )
        if rule is None:
            return Verdict(currency_verdict="unknown")
        return Verdict(
            currency_verdict=rule.get("verdict", "deprecated"),
            finding_type="deprecated_tech",
            severity=rule.get("severity", "medium"),
            rationale=rule.get("rationale", ""),
            confidence=1.0,
            evidence=[{
                "source": f"rule:{rule['id']}",
                "detail": rule.get("rationale", ""),
                "date": "",
            }],
        )

    def recommend(self, entity: CandidateEntity, verdict: Verdict, conn) -> Replacement | None:
        rule = next(
            (r for r in self._rules() if r["id"] == entity.attributes.get("rule_id")),
            None,
        )
        if not rule or not rule.get("replacement"):
            return None
        return Replacement(
            label=rule["replacement"],
            source="rulebook",
            source_ref=f"rule:{rule['id']}",
            detail=rule.get("rationale", ""),
            evidence=[{"source": f"rule:{rule['id']}", "detail": rule.get("rationale", ""), "date": ""}],
        )

    def evidence_snapshot(self, conn) -> str:
        payload = "|".join(f"{r['id']}:{r['pattern']}:{r.get('replacement','')}" for r in self._rules())
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
