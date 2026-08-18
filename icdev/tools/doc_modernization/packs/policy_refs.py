# CUI // SP-CTI
"""Policy/standards reference pack — superseded standards via supersession map.

Deterministic evidence: args/docmod/rulebook_policy.yaml. KG standard/document
nodes corroborate when a graph exists (optional, guarded). Successors come
only from the map (TRUST rule 1).

cef-di-01: the KG corroboration read goes through the governed seam
(tools/doc_modernization/evidence.py -> cortex.resolve, `graph` rung) when
`cortex.enabled` is on, and through the hand-written kg_nodes SELECT otherwise.
It is corroboration in both cases and moves no verdict. The NIST-pubs read is
deliberately NOT migrated: it compares `revision_num` numerically, and an exact
integer is not something a ranked retrieval seam can return.
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
RULEBOOK_PATH = _REPO_ROOT / "args" / "docmod" / "rulebook_policy.yaml"

# Generic NIST SP revision reference: "NIST SP 800-53 Rev. 5" / "SP 800-171r3".
# group(1) -> 'SP 800-53', group(2) -> revision integer. Evaluated against the
# docmod_nist_pubs cache (populated by nist_pubs_sync); spans a rulebook rule
# already claimed are deduped out in extract() so a reference is never counted
# twice. This is what lets a NIST feed refresh flag a newly-superseded revision
# without a human first editing rulebook_policy.yaml.
_NIST_SP_RE = re.compile(
    r"(?i)\b(?:NIST\s+)?(SP\s?800-\d+[A-Za-z]?)\s?"
    r"(?:Rev(?:ision|\.)?\s?|r)(\d+)\b"
)

_cache: dict = {"mtime": None, "rules": None}


def _normalize_pub_id(raw: str) -> str:
    """'SP 800-53' / 'sp800-53' -> canonical 'SP 800-53' (matches nist_pubs_sync)."""
    m = re.match(r"(?i)\s*SP\s?(800-\d+[A-Za-z]?)\s*", raw or "")
    return f"SP {m.group(1)}" if m else (raw or "").strip()


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

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        # Tests inject a frozen datetime here; None => live UTC clock.
        self.clock = None

    def _utcnow(self):
        return self.clock or temporal.utcnow()

    def extract(self, text: str, chunk_ref: ChunkRef) -> list[CandidateEntity]:
        rules = load_supersession_map()
        out, seen = [], set()
        rule_spans: list[tuple[int, int]] = []
        for rule in rules:
            for m in rule["compiled"].finditer(text):
                label = m.group(0).strip()
                key = f"{rule['id']}|{label.lower()}"
                if key in seen:
                    continue
                seen.add(key)
                rule_spans.append((m.start(), m.end()))
                start = max(0, m.start() - 60)
                out.append(CandidateEntity(
                    label=label, entity_type="standard", pack_id=self.pack_id,
                    chunk_ref=chunk_ref, raw_match=label,
                    context=text[start:m.end() + 60].strip(),
                    attributes={"rule_id": rule["id"]},
                ))
        # Additive proactive temporal entities for rules carrying date fields —
        # disjoint from the supersession entities above (dateless rules add none).
        out.extend(temporal.temporal_entities(
            rules, text, chunk_ref, pack_id=self.pack_id, entity_type="standard",
        ))
        # Dynamic NIST-revision candidates from the docmod_nist_pubs cache. A doc
        # may cite a revision that no rulebook rule names yet; evaluate() decides
        # current-vs-superseded against the cache. Skip any span a rulebook rule
        # already claimed so the same reference is never double-counted.
        seen_dyn: set[str] = set()
        for m in _NIST_SP_RE.finditer(text):
            if any(not (m.end() <= s or m.start() >= e) for s, e in rule_spans):
                continue
            pub_id = _normalize_pub_id(m.group(1))
            cited = int(m.group(2))
            dkey = f"{pub_id}|{cited}"
            if dkey in seen_dyn:
                continue
            seen_dyn.add(dkey)
            start = max(0, m.start() - 60)
            out.append(CandidateEntity(
                label=m.group(0).strip(), entity_type="standard", pack_id=self.pack_id,
                chunk_ref=chunk_ref, raw_match=m.group(0).strip(),
                context=text[start:m.end() + 60].strip(),
                attributes={"nist_pub_id": pub_id, "cited_revision": cited},
            ))
        return out

    def _latest_revision(self, pub_id: str, conn) -> dict | None:
        try:
            row = conn.execute(
                "SELECT latest_revision, revision_num, published_date, url "
                "FROM docmod_nist_pubs WHERE pub_id = %s", (pub_id,),
            ).fetchone()
            return dict(row) if row else None
        except Exception:
            try:
                conn.rollback()  # PG: failed statement poisons the transaction
            except Exception:
                pass
            return None

    def _evaluate_nist_pub(self, entity: CandidateEntity, conn) -> Verdict:
        """Deterministic verdict for a cited NIST SP revision vs the feed cache."""
        pub_id = entity.attributes["nist_pub_id"]
        cited = int(entity.attributes.get("cited_revision") or 0)
        latest = self._latest_revision(pub_id, conn)
        if not latest or latest.get("revision_num") is None:
            return Verdict(currency_verdict="unknown")
        latest_num = int(latest["revision_num"])
        if latest_num <= cited:
            return Verdict(currency_verdict="current")
        disp = latest.get("latest_revision") or f"Rev {latest_num}"
        pub_date = latest.get("published_date") or ""
        return Verdict(
            currency_verdict="retired",
            finding_type="superseded_standard",
            severity="high" if latest_num - cited >= 2 else "medium",
            rationale=f"{pub_id} Rev {cited} is superseded by {pub_id} {disp} "
                      f"(NIST publication feed).",
            confidence=1.0,
            evidence=[{
                "source": f"nist_pubs:{pub_id}",
                "detail": f"NIST publishes {pub_id} {disp}"
                          + (f" ({pub_date})" if pub_date else ""),
                "date": pub_date,
            }],
        )

    def _kg_corroboration(self, entity: CandidateEntity, conn) -> list[dict]:
        """Knowledge-graph evidence that the graph knows this standard.

        CORROBORATION ONLY — it has never been able to change a verdict and
        still cannot: the caller appends these to ``Verdict.evidence`` after the
        rulebook has already decided. That is precisely why it is the second
        lookup cef-di-01 migrates: an evidence read with no verdict authority is
        the safest thing to move onto a new seam.

        Governed seam when ``cortex.enabled`` is on (the ``graph`` rung of
        ``cortex.resolve``), the hand-written ``kg_nodes`` SELECT otherwise.
        Both cap at one entry, both are best-effort, and a KG that is absent
        leaves the rulebook evidence standing alone either way.
        """
        try:
            from .. import evidence as docmod_evidence

            bundle = docmod_evidence.resolve_evidence(
                entity.label, entity_type="standard",
            )
            cited = docmod_evidence.graph_citations(bundle, entity.label)
            if cited:
                return cited
        except Exception as exc:  # noqa: BLE001 — the seam must never fail a scan
            logger.debug("docmod policy pack: cortex evidence seam unavailable: %s", exc)

        try:
            row = conn.execute(
                "SELECT id FROM kg_nodes WHERE entity_type = 'standard' AND label = %s LIMIT 1",
                (entity.label,),
            ).fetchone()
            if row:
                return [{
                    "source": f"kg:{dict(row)['id']}",
                    "detail": f"KG standard node for {entity.label}", "date": "",
                }]
        except Exception:
            try:
                conn.rollback()  # PG: failed statement poisons the transaction
            except Exception:
                pass  # no KG — rulebook evidence stands alone
        return []

    def evaluate(self, entity: CandidateEntity, conn) -> Verdict:
        # Dynamic NIST-pubs candidate: numeric revision comparison vs the cache.
        if entity.attributes.get("nist_pub_id"):
            return self._evaluate_nist_pub(entity, conn)
        # Time-bounded verdict when this is a temporal entity; None otherwise.
        tv = temporal.evaluate_temporal(
            entity, load_supersession_map(), self._utcnow(), _engine_config(),
        )
        if tv is not None:
            return tv
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
        # Optional KG corroboration — a matching standard node strengthens context.
        evidence.extend(self._kg_corroboration(entity, conn))
        return Verdict(
            currency_verdict=rule.get("verdict", "retired"),
            finding_type="superseded_standard",
            severity=rule.get("severity", "medium"),
            rationale=rule.get("rationale", ""),
            confidence=1.0,
            evidence=evidence,
        )

    def recommend(self, entity: CandidateEntity, verdict: Verdict, conn) -> Replacement | None:
        # Dynamic NIST-pubs candidate: recommend the latest published revision.
        if entity.attributes.get("nist_pub_id"):
            if not verdict.is_finding:
                return None
            pub_id = entity.attributes["nist_pub_id"]
            latest = self._latest_revision(pub_id, conn)
            if not latest:
                return None
            label = f"{pub_id} {latest.get('latest_revision') or ''}".strip()
            return Replacement(
                label=label, source="nist_pubs", source_ref=f"nist_pubs:{pub_id}",
                detail="Latest revision NIST currently publishes",
                evidence=[{"source": f"nist_pubs:{pub_id}", "detail": label,
                           "date": latest.get("published_date") or ""}],
            )
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
        # Date fields are part of the evidence: editing a sunset_date must
        # re-scan documents, so it has to change this hash.
        payload = "|".join(
            f"{r['id']}:{r['pattern']}:{r.get('superseded_by','')}"
            f":{r.get('effective_date','')}:{r.get('sunset_date','')}:{r.get('review_by','')}"
            for r in load_supersession_map()
        )
        # Fold the NIST-pubs cache in so a feed refresh re-triggers policy scans
        # (mirrors the software pack's EOL-cache snapshot).
        try:
            rows = conn.execute(
                "SELECT pub_id, revision_num FROM docmod_nist_pubs ORDER BY pub_id"
            ).fetchall()
            pub_payload = "|".join(
                f"{r['pub_id']}:{r['revision_num']}" for r in (dict(x) for x in rows)
            )
        except Exception:
            try:
                conn.rollback()  # PG: failed statement poisons the transaction
            except Exception:
                pass
            pub_payload = "no-nist-pubs"
        return hashlib.sha256(f"{payload}||{pub_payload}".encode("utf-8")).hexdigest()
