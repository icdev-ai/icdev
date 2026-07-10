# CUI // SP-CTI
"""Software currency pack — product/version extraction + endoflife.date verdicts.

Evidence source: the docmod_eol_products cache (eol_products_sync). Unmapped
products get verdict 'unknown' — never 'eol' (TRUST: no guessing). Replacement
is the newest supported cycle from the cache; the LLM never invents versions.
"""
from __future__ import annotations

import hashlib
import re

from tools.logging.icdev_logger import get_logger

from ..base_pack import CandidateEntity, ChunkRef, DomainPack, Replacement, Verdict
from ..eol_products_sync import get_product_eol, newest_supported_cycle

logger = get_logger(__name__)

_TODAY_DAYS_AGING = 365


class SoftwarePack(DomainPack):
    pack_id = "software"
    entity_types = ["software_product"]

    def _patterns(self) -> list[re.Pattern]:
        pats = []
        for p in (self.config.get("extraction", {}) or {}).get("patterns", []) or []:
            try:
                pats.append(re.compile(p))
            except re.error as exc:
                logger.warning("docmod software pack: bad regex %r: %s", p, exc)
        return pats

    def _alias_map(self) -> dict:
        return dict(self.config.get("alias_map") or {})

    def extract(self, text: str, chunk_ref: ChunkRef) -> list[CandidateEntity]:
        out, seen = [], set()
        for pattern in self._patterns():
            for m in pattern.finditer(text):
                label = m.group(0).strip()
                if label.lower() in seen:
                    continue
                seen.add(label.lower())
                start = max(0, m.start() - 60)
                out.append(CandidateEntity(
                    label=label, entity_type="software_product",
                    pack_id=self.pack_id, chunk_ref=chunk_ref, raw_match=label,
                    context=text[start:m.end() + 60].strip(),
                ))
        return out

    def evaluate(self, entity: CandidateEntity, conn) -> Verdict:
        from datetime import datetime, timezone

        hit = get_product_eol(entity.label, alias_map=self._alias_map(), conn=conn)
        if isinstance(hit, list):
            hit = hit[0] if len(hit) == 1 else None
        if not hit:
            return Verdict(currency_verdict="unknown")

        eol = str(hit.get("eol_date") or "")[:10]
        today = datetime.now(timezone.utc).date().isoformat()
        evidence = [{
            "source": f"eolcache:{hit.get('id', '')}",
            "detail": f"{hit['product']} {hit['cycle']} EOL {eol or 'not announced'} "
                      f"(source: {hit.get('source')})",
            "date": eol,
        }]
        if eol and eol <= today:
            return Verdict(
                currency_verdict="eol", finding_type="eol_software", severity="high",
                rationale=f"{entity.label} reached end of life on {eol}.",
                confidence=1.0, evidence=evidence,
            )
        eos = str(hit.get("eos_date") or "")[:10]
        if eos and eos <= today:
            return Verdict(
                currency_verdict="deprecated", finding_type="eol_software", severity="medium",
                rationale=f"{entity.label} is out of mainstream support since {eos} "
                          f"(EOL {eol or 'not announced'}).",
                confidence=1.0, evidence=evidence,
            )
        return Verdict(currency_verdict="current", evidence=evidence)

    def recommend(self, entity: CandidateEntity, verdict: Verdict, conn) -> Replacement | None:
        hit = get_product_eol(entity.label, alias_map=self._alias_map(), conn=conn)
        if isinstance(hit, list):
            hit = hit[0] if hit else None
        if not hit:
            return None
        newest = newest_supported_cycle(hit["product"], conn=conn)
        if not newest or str(newest["cycle"]) == str(hit["cycle"]):
            return None
        label = f"{hit['product']} {newest['cycle']}"
        return Replacement(
            label=label, source="catalog", source_ref=f"eolcache:{newest.get('id','')}",
            detail=f"Newest supported cycle (EOL {newest.get('eol_date') or 'not announced'})",
            evidence=[{
                "source": f"eolcache:{newest.get('id','')}",
                "detail": label, "date": str(newest.get("eol_date") or ""),
            }],
        )

    def evidence_snapshot(self, conn) -> str:
        """Hash of the EOL cache contents — cache refresh triggers re-scan."""
        try:
            rows = conn.execute(
                "SELECT product, cycle, eol_date, eos_date FROM docmod_eol_products "
                "ORDER BY product, cycle"
            ).fetchall()
            payload = "|".join(
                f"{r['product']}:{r['cycle']}:{r['eol_date']}:{r['eos_date']}"
                for r in (dict(x) for x in rows)
            )
        except Exception:
            try:
                conn.rollback()  # PG: failed statement poisons the transaction
            except Exception:
                pass
            payload = "no-cache"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
