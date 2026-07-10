# CUI // SP-CTI
"""Policy/standards reference pack — superseded standards via supersession map.

Deterministic evidence: args/docmod/rulebook_policy.yaml. KG standard/document
nodes corroborate when a graph exists (optional, guarded). Successors come
only from the map (TRUST rule 1).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from tools.logging.icdev_logger import get_logger

from ..base_pack import CandidateEntity, ChunkRef, DomainPack, Replacement, Verdict

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
RULEBOOK_PATH = _REPO_ROOT / "args" / "docmod" / "rulebook_policy.yaml"

_cache: dict = {"mtime": None, "rules": None}


def load_supersession_map(path: Path | None = None) -> list[dict]:
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
            logger.warning("docmod policy rulebook: bad rule %s: %s", r.get("id"), exc)
    if path == RULEBOOK_PATH:
        _cache["rules"], _cache["mtime"] = rules, mtime
    return rules


class PolicyRefsPack(DomainPack):
    pack_id = "policy_refs"
    entity_types = ["standard"]

    def extract(self, text: str, chunk_ref: ChunkRef) -> list[CandidateEntity]:
        out, seen = [], set()
        for rule in load_supersession_map():
            for m in rule["compiled"].finditer(text):
                label = m.group(0).strip()
                key = f"{rule['id']}|{label.lower()}"
                if key in seen:
                    continue
                seen.add(key)
                start = max(0, m.start() - 60)
                out.append(CandidateEntity(
                    label=label, entity_type="standard", pack_id=self.pack_id,
                    chunk_ref=chunk_ref, raw_match=label,
                    context=text[start:m.end() + 60].strip(),
                    attributes={"rule_id": rule["id"]},
                ))
        return out

    def evaluate(self, entity: CandidateEntity, conn) -> Verdict:
        rule = next(
            (r for r in load_supersession_map()
             if r["id"] == entity.attributes.get("rule_id")),
            None,
        )
        if rule is None:
            return Verdict(currency_verdict="unknown")
        evidence = [{
            "source": f"rule:{rule['id']}",
            "detail": rule.get("rationale", ""),
            "date": "",
        }]
        # Optional KG corroboration — a matching standard node strengthens context
        try:
            row = conn.execute(
                "SELECT id FROM kg_nodes WHERE entity_type = 'standard' AND label = %s LIMIT 1",
                (entity.label,),
            ).fetchone()
            if row:
                evidence.append({
                    "source": f"kg:{dict(row)['id']}",
                    "detail": f"KG standard node for {entity.label}", "date": "",
                })
        except Exception:
            try:
                conn.rollback()  # PG: failed statement poisons the transaction
            except Exception:
                pass  # no KG — rulebook evidence stands alone
        return Verdict(
            currency_verdict=rule.get("verdict", "retired"),
            finding_type="superseded_standard",
            severity=rule.get("severity", "medium"),
            rationale=rule.get("rationale", ""),
            confidence=1.0,
            evidence=evidence,
        )

    def recommend(self, entity: CandidateEntity, verdict: Verdict, conn) -> Replacement | None:
        rule = next(
            (r for r in load_supersession_map()
             if r["id"] == entity.attributes.get("rule_id")),
            None,
        )
        if not rule or not rule.get("superseded_by"):
            return None
        return Replacement(
            label=rule["superseded_by"], source="rulebook",
            source_ref=f"rule:{rule['id']}", detail=rule.get("rationale", ""),
            evidence=[{"source": f"rule:{rule['id']}", "detail": rule.get("rationale", ""), "date": ""}],
        )

    def evidence_snapshot(self, conn) -> str:
        payload = "|".join(
            f"{r['id']}:{r['pattern']}:{r.get('superseded_by','')}"
            for r in load_supersession_map()
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
