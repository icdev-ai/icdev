# CUI // SP-CTI
"""Generic rulebook-driven pack — a new domain needs YAML only, no Python.

``crypto_protocols`` and ``policy_refs`` are both pure rulebook packs, but each
hardcodes its own ``RULEBOOK_PATH``, so standing up a third rules-driven domain
meant copy-pasting ~115 lines of identical Python. That is the single biggest
barrier to another team covering their own domain.

This pack takes the rulebook path from its own config, so a new domain is:

    args/docmod/packs/<domain>.yaml      # evaluator: ...rulebook_pack.RulebookPack
    args/docmod/rulebook_<domain>.yaml   # the rules

and nothing else. Generate both with::

    icdev scaffold docmod-pack <domain> --flavor rulebook

Deterministic (TRUST rule 1): every verdict and replacement comes from a rule
entry and cites its rule id. No LLM in this module.

Rule shape (all optional except id/pattern)::

    rules:
      - id: tls-11
        pattern: '(?i)\\bTLS[ ]?v?1\\.1\\b'
        entity_type: protocol          # else pack's first entity_type
        verdict: deprecated            # else config default_verdict
        finding_type: deprecated_tech  # else config default_finding_type
        severity: high                 # else config default_severity
        rationale: Deprecated by RFC 8996.
        replacement: TLS 1.2+
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from tools.logging.icdev_logger import get_logger

from .. import temporal
from ..base_pack import CandidateEntity, ChunkRef, DomainPack, Replacement, Verdict

logger = get_logger(__name__)


def _engine_config() -> dict:
    """docmod_config.yaml (for the temporal warning window), best-effort."""
    try:
        from ..pack_loader import load_config
        return load_config()
    except Exception:  # pragma: no cover - config optional
        return {}

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Cache keyed BY PATH. The per-domain packs cache on a single module-level path,
# which silently mis-serves any other rulebook; this class is shared by every
# rulebook domain, so the path must be part of the key.
_cache: dict[str, dict] = {}


def load_rulebook(path: Path) -> list[dict]:
    """Rules with compiled patterns, hot-reloaded when the file changes.

    Cache key is (mtime, size), not mtime alone: mtime resolution is coarse on
    some filesystems, so two edits landing in the same tick would serve stale
    rules — and a stale rulebook silently changes what the pack reports.

    A bad regex is logged and skipped, never fatal: one malformed rule must not
    take down every other domain's sweep.
    """
    import yaml

    if not path.exists():
        logger.warning("docmod rulebook: %s does not exist — pack yields no rules", path)
        return []
    key = str(path)
    st = path.stat()
    stamp = (st.st_mtime, st.st_size)
    hit = _cache.get(key)
    if hit and hit["stamp"] == stamp:
        return hit["rules"]

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules: list[dict] = []
    for r in raw.get("rules", []) or []:
        try:
            rules.append({**r, "compiled": re.compile(r["pattern"])})
        except (KeyError, TypeError, re.error) as exc:
            logger.warning("docmod rulebook %s: bad rule %s: %s", path.name, (r or {}).get("id"), exc)
    _cache[key] = {"stamp": stamp, "rules": rules}
    return rules


class RulebookPack(DomainPack):
    """Rulebook pack parametrized entirely by its YAML config.

    Config keys:
        rulebook_path         path to the rules file, relative to the repo root
        default_verdict       verdict when a rule omits one (default 'deprecated')
        default_finding_type  finding_type when a rule omits one (default 'deprecated_tech')
        default_severity      severity when a rule omits one (default 'medium')
    """

    pack_id = "rulebook"
    label = "Rulebook"
    entity_types = ["protocol"]

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        # Tests inject a frozen datetime here; None => live UTC clock.
        self.clock = None

    def _utcnow(self):
        return self.clock or temporal.utcnow()

    def _rulebook_path(self) -> Path:
        raw = (self.config or {}).get("rulebook_path")
        if not raw:
            # Convention over configuration: args/docmod/rulebook_<pack_id>.yaml
            raw = f"args/docmod/rulebook_{self.pack_id}.yaml"
        p = Path(raw)
        return p if p.is_absolute() else _REPO_ROOT / p

    def _rules(self) -> list[dict]:
        return load_rulebook(self._rulebook_path())

    def _default_entity_type(self) -> str:
        return (self.entity_types or ["protocol"])[0]

    def extract(self, text: str, chunk_ref: ChunkRef) -> list[CandidateEntity]:
        out: list[CandidateEntity] = []
        seen: set[str] = set()
        for rule in self._rules():
            for m in rule["compiled"].finditer(text or ""):
                label = (m.group(0) or "").strip()
                if not label:
                    continue
                key = f"{rule['id']}|{label.lower()}"
                if key in seen:
                    continue
                seen.add(key)
                start = max(0, m.start() - 60)
                out.append(CandidateEntity(
                    label=label,
                    entity_type=rule.get("entity_type", self._default_entity_type()),
                    pack_id=self.pack_id,
                    chunk_ref=chunk_ref,
                    raw_match=label,
                    context=(text or "")[start:m.end() + 60].strip(),
                    # Carries the rule back to evaluate/recommend — the only link
                    # between a match and its evidence.
                    attributes={"rule_id": rule["id"]},
                ))
        # Additive proactive temporal entities for rules carrying date fields —
        # dateless rulebooks (the common case) add none and behave as before.
        out.extend(temporal.temporal_entities(
            self._rules(), text, chunk_ref,
            pack_id=self.pack_id, entity_type=self._default_entity_type(),
        ))
        return out

    def _rule_for(self, entity: CandidateEntity) -> dict | None:
        rid = (entity.attributes or {}).get("rule_id")
        return next((r for r in self._rules() if r["id"] == rid), None)

    def evaluate(self, entity: CandidateEntity, conn) -> Verdict:
        # Time-bounded verdict when this is a temporal entity; None otherwise.
        tv = temporal.evaluate_temporal(
            entity, self._rules(), self._utcnow(), _engine_config(),
        )
        if tv is not None:
            return tv
        rule = self._rule_for(entity)
        if rule is None:
            # The rulebook changed under us between extract and evaluate.
            return Verdict(
                currency_verdict="unknown",
                finding_type=None,
                rationale="Rule no longer present in the rulebook.",
                confidence=0.0,
            )
        cfg = self.config or {}
        return Verdict(
            currency_verdict=rule.get("verdict", cfg.get("default_verdict", "deprecated")),
            finding_type=rule.get("finding_type", cfg.get("default_finding_type", "deprecated_tech")),
            severity=rule.get("severity", cfg.get("default_severity", "medium")),
            rationale=rule.get("rationale", ""),
            confidence=1.0,
            evidence=[{
                "source": f"rule:{rule['id']}",
                "detail": rule.get("rationale", ""),
                "date": "",
            }],
        )

    def recommend(self, entity: CandidateEntity, verdict: Verdict, conn) -> Replacement | None:
        rule = self._rule_for(entity)
        if not rule or not rule.get("replacement"):
            return None
        # source_ref points at the rule: a Replacement must always resolve to a
        # real catalog/rulebook entry, never an invention (TRUST rule 1).
        return Replacement(
            label=str(rule["replacement"]),
            source="rulebook",
            source_ref=f"rule:{rule['id']}",
            detail=rule.get("rationale", ""),
            evidence=[{
                "source": f"rule:{rule['id']}",
                "detail": rule.get("rationale", ""),
                "date": "",
            }],
        )

    def evidence_snapshot(self, conn) -> str:
        """Hash the rules so editing the rulebook re-scans documents.

        The base default hashes static config only, which would never notice a
        rulebook edit — the pack's entire evidence source.
        """
        payload = "|".join(
            f"{r['id']}:{r.get('pattern','')}:{r.get('verdict','')}:{r.get('replacement','')}"
            f":{r.get('effective_date','')}:{r.get('sunset_date','')}:{r.get('review_by','')}"
            for r in self._rules()
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
