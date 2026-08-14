# CUI // SP-CTI
"""KG-to-text constrained validation (trust-kg-01).

The claim tier in ``citation_grounding`` binds a claim to a SPAN of the text it
cites. That catches an assertion its own source does not contain, but it cannot
catch an assertion whose source is simply *wrong*, and it has nothing to say
about the entities a claim names. This module checks claims against GRAPH FACTS:
does the thing exist, is the relation the claim asserts a shape this graph
recognises, and is the specific edge actually there.

**What the graph actually contains, measured 2026-08-14 on the live board.**
The design this module was planned around assumed ``kg_ontology`` supplied a
declared ``(subject_type, predicate, object_type)`` schema — a SHACL-lite.
It does not, because it is EMPTY, and so is everything upstream of it::

    kg_nodes                     8,869 rows      populated
    kg_edges                    16,493 rows      populated, 10+ relationship types
    kg_ontology                      0 rows      inert
    ontology_subclass_closure        0 rows      inert (the source that feeds it)
    kg_nodes.ontology_id             0 populated

So the declared-ontology chain — ``ontology_subclass_closure`` →
``ingester._populate_kg_ontology`` → ``kg_ontology`` →
``graph_rag._load_ontology_relations`` — is inert end to end, while the graph
underneath it is rich. Building schema validation on the empty half would have
produced a checker that answered "unknown" forever while looking like it worked:
this platform's signature defect, reproduced inside the tool meant to catch it.

The type schema is therefore DERIVED FROM THE GRAPH — every
``(subject entity_type, relationship, object entity_type)`` an edge actually
attests. That is a weaker instrument than a declared schema and is treated as
one:

    declared schema : an unlisted signature is provably ILLEGAL     -> may block
    observed schema : an unseen signature is merely UNATTESTED      -> warns only

Conflating those two is how a validator fabricates findings. ``schema_source``
is reported on every result so a caller can never mistake one for the other, and
the moment ``kg_ontology`` gains rows this module upgrades to ``declared`` with
no code change.

Verdicts, and the line that matters most:

    kg_supported     a matching edge exists in the graph
    kg_unattested    schema-plausible, but this graph has no such edge
    kg_contradicted  the graph asserts something incompatible (declared mode only)
    no_entities      nothing in the claim resolved to a node

``kg_unattested`` is NEVER promoted to ``kg_contradicted``. Absence of an edge is
absence of evidence: the graph covers a fraction of the world, and a claim about
something it has never indexed is not thereby false. This is the same discipline
as ``chain_sweep``'s pre_cutover-vs-broken split and ``capability_consumption``'s
``telemetry_available: false``. A graph with no nodes at all reports
``kg_unmeasurable`` rather than marking every claim unsupported — a fresh
worktree must not manufacture findings.

No citation parsing or claim decomposition is re-implemented here; both come
from ``citation_grounding``. The TRUST invariant in CLAUDE.md forbids a second
copy.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from tools.logging.icdev_logger import get_logger
from tools.quality.citation_grounding import decompose_claims, extract_anchors, strip_citations

logger = get_logger(__name__)

VOCABULARY_VERSION = "kg-1.0"

KG_SUPPORTED = "kg_supported"
KG_UNATTESTED = "kg_unattested"
KG_CONTRADICTED = "kg_contradicted"
KG_NO_ENTITIES = "no_entities"

SCHEMA_DECLARED = "declared"      # kg_ontology has rows — an unlisted signature is illegal
SCHEMA_OBSERVED = "observed"      # derived from kg_edges — an unseen signature is only unattested
SCHEMA_UNAVAILABLE = "unavailable"

STATUS_OK = "ok"
STATUS_UNMEASURABLE = "kg_unmeasurable"

# Issue codes. Shaped to match citation_gate / placeholder_findings so one gate
# consumes every guard symmetrically — {item_number, issue, detail}.
ISSUE_UNKNOWN_ENTITY = "unknown_entity"
ISSUE_CONTRADICTED = "kg_contradicted_claim"

_MIN_MENTION_LEN = 2
_MAX_MENTIONS_PER_CLAIM = 12

#: A modelled predicate is identifier-shaped (``uses_table``, ``part_of``).
#: ``kg_edges.relationship`` also carries free-text edge labels — "CUI data",
#: "LLM inference", "scored threats" — and admitting those to the predicate
#: vocabulary makes the extractor match ordinary prose and manufacture triples.
#: Measured on the live board 2026-08-14: of 100 distinct relationships, the 32
#: identifier-shaped ones cover 16,423 of 16,493 edges (99.6%); the 68
#: prose-shaped ones cover 70 edges between them, about one apiece. Filtering
#: costs essentially no coverage and removes the whole false-positive class.
_PREDICATE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

#: Leading determiners are part of a proper-noun anchor ("The Storage tool") but
#: not part of the entity's label. Both spellings are tried when resolving.
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)


# ─── Data model ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class NodeRef:
    node_id: str
    label: str
    entity_type: str
    matched_on: str = "label"       # label | alias | ontology_id

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id, "label": self.label,
            "entity_type": self.entity_type, "matched_on": self.matched_on,
        }


@dataclass(frozen=True)
class Triple:
    subject: NodeRef
    predicate: str
    obj: NodeRef

    def signature(self) -> Tuple[str, str, str]:
        return (self.subject.entity_type, self.predicate, self.obj.entity_type)

    def to_dict(self) -> dict:
        return {
            "subject": self.subject.to_dict(), "predicate": self.predicate,
            "object": self.obj.to_dict(), "signature": list(self.signature()),
        }


@dataclass
class GraphSchema:
    """Which ``(subject_type, predicate, object_type)`` signatures this graph knows."""

    source: str
    signatures: Set[Tuple[str, str, str]] = field(default_factory=set)
    predicates: Tuple[str, ...] = ()

    @property
    def can_block(self) -> bool:
        """Only a DECLARED schema licenses a contradiction verdict.

        An observed schema is incomplete by construction — every signature it
        lacks is one the graph merely has not indexed yet.
        """
        return self.source == SCHEMA_DECLARED

    def permits(self, signature: Tuple[str, str, str]) -> bool:
        return signature in self.signatures


# ─── Graph access ─────────────────────────────────────────────────────────────

def _rows(conn, sql: str, params: Sequence = ()) -> List[dict]:
    try:
        return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    except Exception as exc:  # noqa: BLE001 — a grounding guard must never crash a promote
        logger.warning("kg_grounding: query failed (%s): %s", sql.split()[0], exc)
        return []


def _graph_filter(graph_id: Optional[str]) -> Tuple[str, tuple]:
    return (" WHERE graph_id = %s", (graph_id,)) if graph_id else ("", ())


def graph_is_measurable(conn, *, graph_id: Optional[str] = None) -> bool:
    """False when there is nothing to check against.

    An empty graph cannot disconfirm anything, so reporting every claim
    unsupported would be manufacturing findings out of an empty table — which is
    exactly what a fresh worktree or an ephemeral CI database looks like.
    """
    where, params = _graph_filter(graph_id)
    rows = _rows(conn, f"SELECT COUNT(*) AS n FROM kg_nodes{where}", params)
    return bool(rows) and int(rows[0].get("n") or 0) > 0


def load_schema(conn, *, graph_id: Optional[str] = None) -> GraphSchema:
    """Load the type schema, preferring a DECLARED one and saying which it got.

    ``kg_ontology`` is the declared source. It ships empty (0 rows on the live
    board, and its own upstream ``ontology_subclass_closure`` is empty too), so
    in practice the observed fallback is what runs — and the distinction is
    load-bearing, not cosmetic: it decides whether an unrecognised signature may
    block.
    """
    where, params = _graph_filter(graph_id)
    declared = _rows(
        conn,
        f"SELECT DISTINCT subject_type, predicate, object_type FROM kg_ontology{where}",
        params,
    )
    if declared:
        sigs = {
            (str(r["subject_type"]).lower(), str(r["predicate"]), str(r["object_type"]).lower())
            for r in declared
        }
        return GraphSchema(
            source=SCHEMA_DECLARED,
            signatures=sigs,
            predicates=tuple(sorted({s[1] for s in sigs})),
        )

    # Observed: every type-signature an edge in this graph actually attests.
    ewhere = " WHERE e.graph_id = %s" if graph_id else ""
    observed = _rows(
        conn,
        "SELECT DISTINCT s.entity_type AS st, e.relationship AS p, o.entity_type AS ot "
        "FROM kg_edges e "
        "JOIN kg_nodes s ON s.id = e.source_id "
        "JOIN kg_nodes o ON o.id = e.target_id" + ewhere,
        params,
    )
    if not observed:
        return GraphSchema(source=SCHEMA_UNAVAILABLE)
    sigs = {
        (str(r["st"]).lower(), str(r["p"]), str(r["ot"]).lower())
        for r in observed
        if r.get("st") and r.get("p") and r.get("ot")
        and _PREDICATE_RE.match(str(r["p"]))
    }
    if not sigs:
        return GraphSchema(source=SCHEMA_UNAVAILABLE)
    return GraphSchema(
        source=SCHEMA_OBSERVED,
        signatures=sigs,
        predicates=tuple(sorted({s[1] for s in sigs})),
    )


# ─── Entity resolution ────────────────────────────────────────────────────────

@dataclass
class Lexicon:
    """The graph's own labels, for matching entity mentions in prose.

    Regex NER is the wrong instrument here. ``extract_anchors`` is tuned to find
    factual PARTICLES that must appear in a cited source, not entity NAMES: it
    returns "The Storage" for a node labelled ``Storage`` and misses ``Ch'in
    State`` entirely because an apostrophe ends its proper-noun pattern. Both
    failures silently reduce a claim to "no entities", which reads as "nothing
    to check" rather than "the extractor could not see it".

    Matching against the labels the graph actually holds removes the guessing:
    we only care about entities this graph knows, and it knows their exact
    spelling. ``extract_anchors`` stays as the fallback for callers with no
    connection, and is NOT modified — it is load-bearing for ``claim_guard``.
    """

    by_label: Dict[str, NodeRef] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.by_label)

    def find(self, claim: str) -> List[Tuple[int, str, NodeRef]]:
        """Non-overlapping, longest-first label matches, ordered by position."""
        text = strip_citations(claim or "")
        if not text or not self.by_label:
            return []
        lowered = text.lower()
        hits: List[Tuple[int, str, NodeRef]] = []
        taken: List[Tuple[int, int]] = []
        for label in sorted(self.by_label, key=len, reverse=True):
            start = 0
            while True:
                idx = lowered.find(label, start)
                if idx < 0:
                    break
                end = idx + len(label)
                before_ok = idx == 0 or not (text[idx - 1].isalnum() or text[idx - 1] == "_")
                after_ok = end >= len(text) or not (text[end].isalnum() or text[end] == "_")
                if before_ok and after_ok and not any(idx < te and end > ts for ts, te in taken):
                    taken.append((idx, end))
                    hits.append((idx, text[idx:end], self.by_label[label]))
                start = end
        hits.sort(key=lambda h: h[0])
        return hits


def _is_unambiguous_label(label: str) -> bool:
    """Is this label distinctive enough to match inside prose?

    A single all-lowercase dictionary word — the graph holds nodes labelled
    ``research``, ``chunks``, ``features`` — would match ordinary prose and
    attach a claim to an entity nobody mentioned. Requiring a multi-token label,
    an uppercase letter, a digit, or an identifier separator biases this toward
    false negatives, which is the correct bias for a gate: a missed entity
    weakens one check, an invented one fabricates a finding.
    """
    lab = (label or "").strip()
    if len(lab) < 3:
        return False
    return bool(
        " " in lab or any(c.isupper() for c in lab)
        or any(c.isdigit() for c in lab) or "_" in lab or "-" in lab
    )


def load_lexicon(conn, *, graph_id: Optional[str] = None, limit: int = 50_000) -> Lexicon:
    """Load distinctive node labels once per artifact.

    ``limit`` is a memory bound, not a sampling strategy: it is far above the
    live graph (8,869 nodes) and exists so an unexpectedly huge graph degrades
    to partial coverage rather than exhausting the process. Partial coverage
    costs recall — it can never invent a match.
    """
    where, params = _graph_filter(graph_id)
    rows = _rows(
        conn,
        f"SELECT id, label, entity_type FROM kg_nodes{where} LIMIT {int(limit)}",  # nosec B608 - int-cast bound
        params,
    )
    by_label: Dict[str, NodeRef] = {}
    for r in rows:
        label = str(r.get("label") or "").strip()
        if not _is_unambiguous_label(label):
            continue
        key = label.lower()
        if key in by_label:
            continue  # first wins; ambiguous labels are not worth guessing between
        by_label[key] = NodeRef(
            node_id=str(r["id"]), label=label,
            entity_type=str(r.get("entity_type") or "").lower(), matched_on="label",
        )
    return Lexicon(by_label=by_label)


def extract_entity_mentions(claim: str) -> List[str]:
    """Candidate entity names in a claim, WITHOUT a graph connection.

    Fallback path for callers that have no connection. Reuses
    ``citation_grounding.extract_anchors`` and drops purely numeric anchors,
    which are quantities rather than entities. Prefer :func:`load_lexicon` +
    ``Lexicon.find`` when a connection is available — it is strictly more
    accurate, because it matches the labels the graph actually holds instead of
    guessing where an entity name starts and ends.
    """
    out: List[str] = []
    for anchor in extract_anchors(claim or ""):
        if len(anchor) < _MIN_MENTION_LEN or not re.search(r"[A-Za-z]", anchor):
            continue  # a bare quantity is not an entity
        for variant in (anchor, _LEADING_ARTICLE_RE.sub("", anchor).strip()):
            # "The Storage tool imports…" yields the anchor "The Storage", but
            # the node is labelled "Storage". Try both rather than lose the
            # resolution — an unresolved mention silently weakens every check
            # downstream of it.
            if variant and len(variant) >= _MIN_MENTION_LEN and variant not in out:
                out.append(variant)
    return out[:_MAX_MENTIONS_PER_CLAIM]


def resolve_entities(
    mentions: Iterable[str], *, conn, graph_id: Optional[str] = None
) -> Dict[str, Optional[NodeRef]]:
    """Map each mention to a graph node, or to None when nothing matches.

    Exact case-insensitive label match first, then the alias table when one is
    present. Deliberately NOT fuzzy: a near-miss resolution would attach a claim
    to the wrong entity and then confidently validate it against that entity's
    edges, which is worse than not resolving at all.
    """
    wanted = [m for m in dict.fromkeys(mentions) if m]
    out: Dict[str, Optional[NodeRef]] = {m: None for m in wanted}
    if not wanted:
        return out

    placeholders = ",".join("%s" for _ in wanted)
    where, gparams = _graph_filter(graph_id)
    gclause = " AND graph_id = %s" if graph_id else ""
    rows = _rows(
        conn,
        f"SELECT id, label, entity_type FROM kg_nodes "
        f"WHERE LOWER(label) IN ({placeholders}){gclause}",  # nosec B608 - placeholders only
        tuple(m.lower() for m in wanted) + gparams,
    )
    by_label = {str(r["label"]).lower(): r for r in rows}
    for m in wanted:
        r = by_label.get(m.lower())
        if r:
            out[m] = NodeRef(
                node_id=str(r["id"]), label=str(r["label"]),
                entity_type=str(r.get("entity_type") or "").lower(), matched_on="label",
            )

    unresolved = [m for m, v in out.items() if v is None]
    if unresolved:
        for m, ref in _resolve_via_alias(unresolved, conn=conn, graph_id=graph_id).items():
            out[m] = ref
    return out


def _resolve_via_alias(
    mentions: Sequence[str], *, conn, graph_id: Optional[str] = None
) -> Dict[str, Optional[NodeRef]]:
    """Second-chance resolution through node aliases.

    There is NO alias table. ``disambiguator.add_alias`` stores aliases inside
    ``kg_nodes.properties`` as a JSON list under the ``"aliases"`` key, and
    ``merge_entities`` appends the losing node's label there. Querying a
    ``kg_entity_aliases`` table would have been silently dead code — the exact
    defect this module exists to catch.

    ``properties`` is TEXT-encoded JSON, not ``jsonb``, so the filtering is a
    cheap SQL ``LIKE`` to narrow candidates and the actual match is done in
    Python with ``json.loads`` — per the CLAUDE.md rule that runtime call sites
    must not author SQLite-dialect JSON SQL and lean on ``translate_sql``.
    """
    import json

    out: Dict[str, Optional[NodeRef]] = {m: None for m in mentions}
    gclause = " AND graph_id = %s" if graph_id else ""
    _, gparams = _graph_filter(graph_id)

    for mention in mentions:
        rows = _rows(
            conn,
            "SELECT id, label, entity_type, properties FROM kg_nodes "
            "WHERE properties LIKE %s" + gclause + " LIMIT 200",
            (f"%{mention}%",) + gparams,
        )
        for r in rows:
            raw = r.get("properties")
            try:
                props = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (ValueError, TypeError):
                continue
            aliases = props.get("aliases") if isinstance(props, dict) else None
            if not isinstance(aliases, list):
                continue
            if any(str(a).lower() == mention.lower() for a in aliases):
                out[mention] = NodeRef(
                    node_id=str(r["id"]), label=str(r["label"]),
                    entity_type=str(r.get("entity_type") or "").lower(),
                    matched_on="alias",
                )
                break
    return out


# ─── Relation extraction ──────────────────────────────────────────────────────

def extract_relation_triples(
    claim: str,
    resolved: "Dict[str, Optional[NodeRef]] | Sequence[Tuple[int, str, NodeRef]]",
    *,
    predicates: Sequence[str],
) -> List[Triple]:
    """Triples asserted by a claim, over a CLOSED predicate vocabulary.

    ``predicates`` comes from the graph itself (``GraphSchema.predicates``), so
    the extractor is structurally incapable of inventing a relation — the
    deterministic-picker discipline from ``categorical_scoring``. A triple is
    emitted only when a recognised predicate sits BETWEEN two resolved entities
    in reading order; two entities with nothing recognisable between them yield
    no triple rather than a guessed one.

    ``resolved`` accepts either the ``Lexicon.find`` result (already positioned)
    or the ``{mention: NodeRef}`` mapping from :func:`resolve_entities`.
    """
    text = strip_citations(claim or "")
    if isinstance(resolved, dict):
        lowered = text.lower()
        positions = [
            (lowered.find(m.lower()), r)
            for m, r in resolved.items()
            if r is not None and lowered.find(m.lower()) >= 0
        ]
    else:
        positions = [(pos, ref) for pos, _surface, ref in resolved]
    positions.sort(key=lambda t: t[0])
    if len(positions) < 2 or not predicates:
        return []

    triples: List[Triple] = []
    for (s_idx, s_ref), (o_idx, o_ref) in zip(positions, positions[1:]):
        between = text[s_idx:o_idx].lower()
        hit = _match_predicate(between, predicates)
        if hit and s_ref.node_id != o_ref.node_id:
            triples.append(Triple(subject=s_ref, predicate=hit, obj=o_ref))
    return triples


def _match_predicate(span: str, predicates: Sequence[str]) -> Optional[str]:
    """Longest recognised predicate appearing in ``span``, or None.

    Underscores in graph relationships (``uses_table``) are matched against
    spaces in prose (``uses table``), which is the only normalisation applied —
    anything looser starts inventing relations.
    """
    best: Optional[str] = None
    for p in predicates:
        surface = str(p).replace("_", " ").strip().lower()
        if not surface:
            continue
        if re.search(rf"\b{re.escape(surface)}\b", span):
            if best is None or len(surface) > len(str(best).replace("_", " ")):
                best = p
    return best


# ─── Validation ───────────────────────────────────────────────────────────────

def validate_triple(
    triple: Triple, *, conn, schema: GraphSchema, graph_id: Optional[str] = None
) -> dict:
    """Check one triple against the graph. Returns ``{verdict, reason, ...}``.

    Order matters: edge existence is checked FIRST, because an attested edge
    settles the question regardless of what the schema does or does not list.
    """
    gclause = " AND graph_id = %s" if graph_id else ""
    _, gparams = _graph_filter(graph_id)
    edge = _rows(
        conn,
        f"SELECT id FROM kg_edges WHERE source_id = %s AND target_id = %s "
        f"AND relationship = %s{gclause} LIMIT 1",
        (triple.subject.node_id, triple.obj.node_id, triple.predicate) + gparams,
    )
    if edge:
        return {"verdict": KG_SUPPORTED, "reason": "edge_present", "triple": triple.to_dict()}

    signature = triple.signature()
    if schema.can_block and not schema.permits(signature):
        # A DECLARED schema that does not list this signature makes the claim
        # type-invalid: it asserts a relation the ontology forbids between these
        # kinds of thing. Provable without a reasoner.
        return {
            "verdict": KG_CONTRADICTED, "reason": "signature_not_permitted",
            "triple": triple.to_dict(), "schema_source": schema.source,
        }

    # No edge, and the schema cannot license a stronger statement. Absence of
    # evidence — never promoted to contradiction (see module docstring).
    return {
        "verdict": KG_UNATTESTED,
        "reason": "no_matching_edge" if schema.permits(signature) else "signature_unseen",
        "triple": triple.to_dict(), "schema_source": schema.source,
    }


def kg_ground_claims(
    text: str, *, conn, graph_id: Optional[str] = None, schema: Optional[GraphSchema] = None
) -> dict:
    """Per-claim KG grounding report for a whole artifact.

    Returns ``{status, schema_source, claims, counts, unknown_entities,
    vocabulary_version}``. ``status`` is ``kg_unmeasurable`` when the graph has
    no nodes — the caller's profile decides what that means, and it is never
    silently a pass.
    """
    base = {
        "status": STATUS_OK, "schema_source": SCHEMA_UNAVAILABLE, "claims": [],
        "counts": {KG_SUPPORTED: 0, KG_UNATTESTED: 0, KG_CONTRADICTED: 0, KG_NO_ENTITIES: 0},
        "unknown_entities": [], "vocabulary_version": VOCABULARY_VERSION,
    }
    if not (text or "").strip():
        return base
    if not graph_is_measurable(conn, graph_id=graph_id):
        return {**base, "status": STATUS_UNMEASURABLE,
                "detail": "graph has no nodes; nothing to validate against"}

    schema = schema or load_schema(conn, graph_id=graph_id)
    base["schema_source"] = schema.source
    lexicon = load_lexicon(conn, graph_id=graph_id)

    claims: List[dict] = []
    counts = dict(base["counts"])
    unknown: List[str] = []

    for i, (claim, _s, _e) in enumerate(decompose_claims(text), start=1):
        hits = lexicon.find(claim)
        # Anchors the graph does NOT know. Reported, never blocking by default:
        # the graph indexes a fraction of the world, so an unrecognised proper
        # noun is far more often out-of-scope than fabricated.
        matched = {surface.lower() for _p, surface, _r in hits}
        missing = [
            m for m in extract_entity_mentions(claim)
            if m.lower() not in matched
            and not any(m.lower() in s for s in matched)
        ]
        unknown.extend(m for m in missing if m not in unknown)

        triples = extract_relation_triples(claim, hits, predicates=schema.predicates)
        results = [validate_triple(t, conn=conn, schema=schema, graph_id=graph_id) for t in triples]

        if not results:
            verdict = KG_NO_ENTITIES
        elif any(r["verdict"] == KG_CONTRADICTED for r in results):
            verdict = KG_CONTRADICTED
        elif all(r["verdict"] == KG_SUPPORTED for r in results):
            verdict = KG_SUPPORTED
        else:
            verdict = KG_UNATTESTED
        counts[verdict] = counts.get(verdict, 0) + 1
        claims.append({
            "item_number": i, "claim": claim, "verdict": verdict,
            "unknown_entities": missing, "triples": results,
        })

    return {**base, "claims": claims, "counts": counts, "unknown_entities": unknown,
            "schema_source": schema.source}


def kg_gate(report: dict, *, flag_unknown_entities: bool = False) -> List[dict]:
    """Turn a :func:`kg_ground_claims` report into blocking findings.

    Mirrors ``citation_gate`` / ``claim_gate`` / ``placeholder_findings`` —
    ``{item_number, issue, detail}`` — so a promote gate consumes all of them
    symmetrically instead of special-casing each.

    Only ``kg_contradicted`` produces a finding by default, and that verdict is
    only reachable under a DECLARED schema. ``unknown_entity`` is opt-in
    (``flag_unknown_entities``) because a graph indexing a fraction of the world
    would otherwise flag every proper noun it has not seen — noise that would
    get the whole guard switched off.
    """
    findings: List[dict] = []
    for c in report.get("claims") or []:
        if c.get("verdict") == KG_CONTRADICTED:
            findings.append({
                "item_number": c.get("item_number"),
                "issue": ISSUE_CONTRADICTED,
                "detail": [
                    t["triple"]["signature"] for t in (c.get("triples") or [])
                    if t.get("verdict") == KG_CONTRADICTED
                ],
            })
        elif flag_unknown_entities and c.get("unknown_entities"):
            findings.append({
                "item_number": c.get("item_number"),
                "issue": ISSUE_UNKNOWN_ENTITY,
                "detail": c["unknown_entities"],
            })
    return findings
