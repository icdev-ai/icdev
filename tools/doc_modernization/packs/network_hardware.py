# CUI // SP-CTI
"""Network hardware currency pack — the flagship pack for the user's stale
Network Architecture Standard problem.

Extraction: pack-YAML regexes + the live inventory vocabulary (distinct
ni_devices vendor/model strings and nc_device_profiles platforms via the
CatalogProvider adapter).

Evidence priority (deterministic — TRUST rule 1):
1. Curated catalog (NetworkCatalogAdapter over team-maintained
   nc_hardware_profiles, merged with the central generic store) — AUTHORITATIVE
2. tools/migration_canvas/eol_sync.get_eol_date (Cisco EoX + eol_data.yaml seed)
3. The domain-agnostic entity-currency store, consulted only when 1 and 2 both
   come back empty. It is the seam for a provider neither of them knows about:
   a source declared in args/entity_currency.yaml starts answering here with no
   change to this file, which is the whole point of a source-agnostic store. It
   never overrides the curated catalog — it is not even consulted when the
   catalog answered.
   Since cef-di-01 this lookup goes through the GOVERNED seam
   (tools/doc_modernization/evidence.py -> cortex.resolve) when
   `cortex.enabled` is on in args/docmod/docmod_config.yaml, and through the
   direct tools/currency/entity_currency call otherwise. Same store, same typed
   fields, same verdict derivation below — what the seam adds is the TRUST
   chain, an audit row, a registered evidence set, and the RAG/DIC/KG/KB rungs
   this pack could never reach by hand. See _currency_hit().
4. Learned de facto stats (defacto_learner) — corroboration + replacement
   tie-breaker; divergence/gap findings are emitted by the sweep via
   defacto_learner.cross_check, not per-document here.
"""
from __future__ import annotations

import hashlib
import re

from tools.logging.icdev_logger import get_logger

from ..base_pack import CandidateEntity, ChunkRef, DomainPack, Replacement, Verdict
from ..catalog import MergedCatalog

logger = get_logger(__name__)

DOMAIN = "network_hardware"


class NetworkHardwarePack(DomainPack):
    pack_id = "network_hardware"
    entity_types = ["hardware_model"]

    def __init__(self, config: dict | None = None, catalog=None):
        super().__init__(config)
        self._catalog = catalog or MergedCatalog()
        self._vocab_patterns: list[re.Pattern] | None = None

    # ── extraction ────────────────────────────────────────────────────────

    def _patterns(self, conn=None) -> list[re.Pattern]:
        pats: list[re.Pattern] = []
        for p in (self.config.get("extraction", {}) or {}).get("patterns", []) or []:
            try:
                pats.append(re.compile(p))
            except re.error as exc:
                logger.warning("docmod hw pack: bad regex %r: %s", p, exc)
        if (self.config.get("extraction", {}) or {}).get("inventory_terms", False):
            pats.extend(self._inventory_vocab(conn))
        return pats

    def _inventory_vocab(self, conn=None) -> list[re.Pattern]:
        """Distinct inventory model strings as literal patterns (cached per run)."""
        if self._vocab_patterns is not None:
            return self._vocab_patterns
        terms: set[str] = set()
        if conn is not None:
            try:
                for r in conn.execute(
                    "SELECT DISTINCT model FROM ni_devices "
                    "WHERE model IS NOT NULL AND model != ''"
                ).fetchall():
                    terms.add(dict(r)["model"].strip())
            except Exception as exc:
                logger.warning("docmod hw pack: inventory vocab failed: %s", exc)
                try:
                    conn.rollback()  # PG: failed statement poisons the transaction
                except Exception:
                    pass
        for provider in getattr(self._catalog, "providers", []):
            vocab = getattr(provider, "vocabulary", None)
            if callable(vocab):
                try:
                    terms.update(model for _, model in vocab() if model)
                except Exception:
                    pass
        self._vocab_patterns = [
            re.compile(r"(?i)\b" + re.escape(t) + r"\b") for t in terms if len(t) >= 4
        ]
        return self._vocab_patterns

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
                    label=label, entity_type="hardware_model",
                    pack_id=self.pack_id, chunk_ref=chunk_ref, raw_match=label,
                    context=text[start:m.end() + 60].strip(),
                ))
        return out

    # ── evaluation ────────────────────────────────────────────────────────

    def _currency_hit(self, label: str, conn) -> tuple:
        """The entity-currency answer for ``label``, plus WHICH seam produced it.

        Returns ``(hit | None, via)`` where ``hit`` is
        ``entity_currency.resolve``'s dict shape either way — the caller reads
        the same keys and derives the same verdict from them, which is the whole
        design of the cef-di-01 migration.

        Order, and why:

        1. ``cortex.resolve`` when ``cortex.enabled`` is on. Its ``currency``
           backend reads the SAME store this pack read directly, so the fields
           are the same fields; what is added is the TRUST chain, the
           ``cortex_audit`` row and a registered evidence set, plus the RAG/DIC/
           KG/KB rungs the pack could never reach on its own.
        2. The direct store call, unchanged, when the toggle is off, when the
           seam is re-entrant (we are inside a resolution that is running this
           very pack), or when the seam had nothing. The fallback is what keeps
           the migration additive: the toggle can add an answer where there was
           none, and cannot take one away.

        No LLM on either branch (TRUST rule 1): ``resolve`` passes
        ``corrective=False`` and makes no model call, and this method reads
        typed fields, never prose.
        """
        try:
            from .. import evidence as docmod_evidence

            bundle = docmod_evidence.resolve_evidence(
                label, entity_type="hardware_model",
            )
            hit = docmod_evidence.currency_assertion(bundle)
            if hit:
                return hit, "cortex.resolve"
        except Exception as exc:  # noqa: BLE001 — the seam must never fail a scan
            logger.debug("docmod hw pack: cortex evidence seam unavailable: %s", exc)

        from tools.currency.entity_currency import resolve

        return resolve(label, entity_type="hardware_model", conn=conn), "entity_currency"

    def evaluate(self, entity: CandidateEntity, conn) -> Verdict:
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date().isoformat()

        entry = self._catalog.lookup(DOMAIN, entity.label)
        evidence: list[dict] = []
        eol_date = eos_date = None
        if entry is not None:
            evidence.append(entry.citation())
            eol_date = (entry.eol_date or "")[:10] or None
            eos_date = (entry.eos_date or "")[:10] or None
            if entry.status in ("deprecated", "retired") and not (eol_date or eos_date):
                return Verdict(
                    currency_verdict=entry.status, finding_type="eol_hardware",
                    severity="high" if entry.status == "retired" else "medium",
                    rationale=f"{entity.label} is marked '{entry.status}' in the curated catalog.",
                    confidence=1.0, evidence=evidence,
                )

        if eol_date is None and eos_date is None:
            # Fall back to the migration-canvas EOL database (Cisco EoX + seed)
            try:
                from tools.migration_canvas.eol_sync import get_eol_date
                hit = get_eol_date(entity.label)
                if hit:
                    eol_date = (hit.get("eol_date") or "")[:10] or None
                    eos_date = (hit.get("eos_date") or "")[:10] or None
                    evidence.append({
                        "source": f"mc_net_eol:{hit.get('vendor','')}:{hit.get('model_pattern','')}",
                        "detail": f"EOL {eol_date or 'n/a'} / EOS {eos_date or 'n/a'} "
                                  f"({hit.get('source', 'eol_data.yaml')})",
                        "date": eol_date or eos_date or "",
                    })
            except Exception as exc:
                logger.debug("docmod hw pack: eol_sync unavailable: %s", exc)

        if eol_date is None and eos_date is None:
            # Last: whatever any OTHER declared provider knows (cef-fnd-04).
            # Reached only when the catalog and the hardware EOL feed both had
            # nothing, so it can add evidence and can never overrule authority.
            #
            # cef-di-01 — this lookup is the one place this pack reaches OUTSIDE
            # its own domain, and it is the lookup that now goes through the
            # governed seam. `_currency_hit` asks cortex.resolve() when the
            # toggle is on and falls back to the direct store call otherwise, in
            # BOTH cases returning the identical dict shape. Everything below
            # this line is untouched: the verdict is still derived here, from
            # typed fields, with no LLM anywhere (TRUST rule 1).
            try:
                hit, hit_via = self._currency_hit(entity.label, conn)
                if hit:
                    eol_date = hit.get("eol_date") or None
                    eos_date = hit.get("eos_date") or None
                    evidence.append({
                        "source": f"entity_currency:{hit.get('source')}",
                        "detail": f"{hit.get('verdict')} as of {hit.get('as_of')}"
                                  + (" (sources disagree)" if hit.get("conflict") else ""),
                        "date": eol_date or eos_date or "",
                        # WHICH seam produced this evidence. A separate key
                        # rather than a suffix on `detail`, so the finding text a
                        # reviewer reads is identical either way and only the
                        # provenance differs — that is what makes a toggle-on and
                        # a toggle-off rescan comparable line for line.
                        "via": hit_via,
                    })
                    # A source may assert a verdict WITHOUT a date (a curated
                    # status word does exactly that). Falling through to the
                    # date checks below would silently discard it and report
                    # 'unknown', so the verdict is honoured here instead.
                    if not (eol_date or eos_date):
                        verdict_map = {
                            "end_of_life": ("eol", "high"),
                            "end_of_support": ("deprecated", "medium"),
                            "deprecated": ("deprecated", "medium"),
                        }
                        mapped = verdict_map.get(str(hit.get("verdict")))
                        if mapped:
                            return Verdict(
                                currency_verdict=mapped[0], finding_type="eol_hardware",
                                severity=mapped[1],
                                rationale=f"{entity.label} is '{hit.get('verdict')}' per "
                                          f"{hit.get('source')} (as of {hit.get('as_of')}).",
                                confidence=float(hit.get("confidence") or 0.0),
                                evidence=evidence,
                            )
            except Exception as exc:
                logger.debug("docmod hw pack: entity_currency unavailable: %s", exc)

        if eol_date and eol_date <= today:
            return Verdict(
                currency_verdict="eol", finding_type="eol_hardware", severity="high",
                rationale=f"{entity.label} reached end of life on {eol_date}.",
                confidence=1.0, evidence=evidence,
            )
        if eos_date and eos_date <= today:
            return Verdict(
                currency_verdict="deprecated", finding_type="eol_hardware", severity="medium",
                rationale=f"{entity.label} is past end of sale/support ({eos_date}); "
                          f"EOL {eol_date or 'not announced'}.",
                confidence=1.0, evidence=evidence,
            )
        if entry is not None:
            return Verdict(currency_verdict="current", evidence=evidence)
        return Verdict(currency_verdict="unknown", evidence=evidence)

    # ── recommendation ────────────────────────────────────────────────────

    def recommend(self, entity: CandidateEntity, verdict: Verdict, conn) -> Replacement | None:
        stale = self._catalog.lookup(DOMAIN, entity.label)
        category = stale.category if stale else None

        # 1. Curated catalog: explicit replacement chain, else approved entry
        #    in the same category (authoritative — user decision).
        if stale is not None and stale.replacement_entry_id:
            for e in self._catalog.get_approved(DOMAIN):
                if e.entry_id == stale.replacement_entry_id:
                    return Replacement(
                        label=f"{e.vendor or ''} {e.product}".strip(), source="catalog",
                        source_ref=e.citation()["source"],
                        detail="Curated replacement chain", evidence=[e.citation()],
                    )
        approved = self._catalog.get_approved(DOMAIN, category) if category else []

        # 2. Learned de facto stats as tie-breaker among approved entries
        #    (or sole source when the catalog has no category entry).
        learned = None
        try:
            from ..defacto_learner import get_recommended
            learned = get_recommended(category or "", conn=conn)
        except Exception:
            pass

        if approved:
            pick = approved[0]
            if learned:
                for e in approved:
                    if e.product.lower() == (learned.get("product") or "").lower():
                        pick = e  # deployment reality corroborates this entry
                        break
            ev = [pick.citation()]
            if learned:
                ev.append({
                    "source": f"defacto:{learned.get('id','')}",
                    "detail": f"{learned['product']} = {learned.get('share_pct', 0)}% of "
                              f"deployed {category} devices",
                    "date": str(learned.get("computed_at", ""))[:10],
                })
            return Replacement(
                label=f"{pick.vendor or ''} {pick.product}".strip(), source="catalog",
                source_ref=pick.citation()["source"],
                detail="Curated approved standard for this device category", evidence=ev,
            )
        if learned:
            return Replacement(
                label=f"{learned.get('vendor') or ''} {learned['product']}".strip(),
                source="defacto", source_ref=f"defacto:{learned.get('id','')}",
                detail=f"Most-deployed {category} model "
                       f"({learned.get('share_pct', 0)}% share) — catalog has no entry "
                       f"(candidate for propose-from-defacto)",
                evidence=[{
                    "source": f"defacto:{learned.get('id','')}",
                    "detail": f"{learned['product']} deploy_count={learned.get('deploy_count')}",
                    "date": str(learned.get("computed_at", ""))[:10],
                }],
            )
        return None

    def evidence_snapshot(self, conn) -> str:
        """Hash over catalog entries + EOL rows + learner output — any change
        re-triggers document scans."""
        parts = []
        try:
            for e in (self._catalog.get_approved(DOMAIN) + self._catalog.get_deprecated(DOMAIN)):
                parts.append(f"{e.provider_source}:{e.entry_id}:{e.status}:{e.eol_date}:{e.eos_date}")
        except Exception:
            parts.append("catalog-error")
        try:
            rows = conn.execute(
                "SELECT vendor, model_pattern, eol_date, eos_date FROM mc_net_eol_data "
                "ORDER BY vendor, model_pattern"
            ).fetchall()
            parts.extend(
                f"eol:{r['vendor']}:{r['model_pattern']}:{r['eol_date']}:{r['eos_date']}"
                for r in (dict(x) for x in rows)
            )
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            parts.append("no-mc-eol")
        try:
            rows = conn.execute(
                "SELECT category, product, share_pct FROM docmod_defacto_standards "
                "WHERE domain = %s ORDER BY category, product", (DOMAIN,)
            ).fetchall()
            parts.extend(
                f"dfs:{r['category']}:{r['product']}:{r['share_pct']}"
                for r in (dict(x) for x in rows)
            )
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            parts.append("no-defacto")
        return hashlib.sha256("|".join(sorted(parts)).encode("utf-8")).hexdigest()
