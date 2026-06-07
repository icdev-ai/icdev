# CUI // SP-CTI
"""DIC GraphRAG community-level retrieval — test corpus fixtures.

Part of the ``dic-adapt-07-d4`` epic. GraphRAG answers *global* ("corpus-level")
questions by first clustering a document corpus into communities, precomputing a
grounded summary per community, then aggregating the relevant community
summaries at query time. Every test in this module needs the *same* corpus so
that community structure — and therefore the answers — are reproducible.

This task (``dic-adapt-07-d4-d1``) owns the deterministic seed:

* :func:`setup_corpus` — a pytest fixture that seeds a small, fixed corpus into
  the DIC document store + the community index (``dic_community_summaries``)
  *before any test logic runs*, and removes it afterwards.
* :func:`seed_corpus` — the underlying, side-effect-free-on-import seeder that
  the fixture (and any non-pytest caller) can reuse.
* :class:`SeededCorpus` — the contract returned to the tests: the collection,
  the seeded document/chunk ids, and the *expected* community → document map
  the precompute (``-d2``) and corpus-level query (``-d3``) steps assert against.

The corpus is intentionally split into two thematically distinct communities
(``comm_budget`` and ``comm_security``) of two documents each, so a community
detector has an unambiguous, deterministic grouping to recover.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import pytest

from tools.db.storage import get_connection

# --------------------------------------------------------------------------- #
# Deterministic seed data
# --------------------------------------------------------------------------- #

#: Dedicated collection so the corpus never collides with real DIC documents.
CORPUS_COLLECTION_ID = "dic_corpus_test"
CORPUS_TENANT_ID = "default"
CORPUS_CLASSIFICATION = "CUI"

#: Fixed timestamp — no wall-clock reads keep the seed fully reproducible.
_SEED_TS = "2026-01-01T00:00:00+00:00"

#: The corpus: each document belongs to exactly one community. Two coherent
#: themes (budget / security), two documents each → two recoverable clusters.
_CORPUS_DOCS: tuple[dict, ...] = (
    {
        "doc_id": "dic_doc_corpus_budget_a",
        "title": "FY26 Program Budget Overview",
        "community_id": "comm_budget",
        "content": (
            "The FY26 program budget increased 12% to fund three new "
            "engineering teams and expanded cloud capacity for the year."
        ),
    },
    {
        "doc_id": "dic_doc_corpus_budget_b",
        "title": "Quarterly Spend Reconciliation",
        "community_id": "comm_budget",
        "content": (
            "Q3 spend reconciliation shows hiring and cloud capacity as the "
            "two largest budget drivers across the fiscal year."
        ),
    },
    {
        "doc_id": "dic_doc_corpus_security_a",
        "title": "Zero Trust Access Policy",
        "community_id": "comm_security",
        "content": (
            "The zero trust access policy mandates mutual TLS and per-request "
            "authorization across every service boundary in the platform."
        ),
    },
    {
        "doc_id": "dic_doc_corpus_security_b",
        "title": "Continuous ATO Controls Catalog",
        "community_id": "comm_security",
        "content": (
            "The continuous ATO controls catalog maps NIST 800-53 access "
            "controls to automated zero trust enforcement points."
        ),
    },
)


# --------------------------------------------------------------------------- #
# Corpus contract
# --------------------------------------------------------------------------- #

@dataclass
class SeededCorpus:
    """Everything the downstream precompute / query tests need to know.

    ``communities`` is the *expected* grouping the seed encodes; the community
    index (``dic_community_summaries``) starts empty so ``-d2`` can populate it
    by running the precompute pipeline and compare against this map.
    """

    collection_id: str
    tenant_id: str
    classification: str
    documents: list[dict] = field(default_factory=list)
    doc_ids: list[str] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)
    communities: dict[str, list[str]] = field(default_factory=dict)

    @property
    def community_ids(self) -> list[str]:
        return sorted(self.communities)

    @property
    def doc_count(self) -> int:
        return len(self.doc_ids)


# --------------------------------------------------------------------------- #
# Schema (idempotent, self-contained so the fixture is conftest-independent)
# --------------------------------------------------------------------------- #

# SQLite-flavoured DDL — tests force ICDEV_STORAGE_BACKEND=sqlite (see conftest).
# IF NOT EXISTS everywhere: never clobbers a richer real table when one exists.
_SCHEMA_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS dic_collections (
        collection_id   TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        description     TEXT DEFAULT '',
        owner_id        TEXT DEFAULT '',
        retention_days  INTEGER DEFAULT 90,
        classification  TEXT DEFAULT 'CUI',
        tenant_id       TEXT DEFAULT 'default',
        created_at      TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dic_documents (
        doc_id          TEXT PRIMARY KEY,
        collection_id   TEXT NOT NULL,
        source_id       TEXT,
        filename        TEXT,
        filepath        TEXT,
        content_type    TEXT,
        provider        TEXT,
        title           TEXT,
        byte_size       INTEGER,
        content_sha256  TEXT,
        page_count      INTEGER DEFAULT 1,
        created_at      TEXT NOT NULL,
        tenant_id       TEXT,
        classification  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dic_versions (
        version_id      TEXT PRIMARY KEY,
        doc_id          TEXT NOT NULL,
        version_no      INTEGER NOT NULL DEFAULT 1,
        origin          TEXT NOT NULL DEFAULT 'human_authored',
        status          TEXT NOT NULL DEFAULT 'approved',
        content_sha256  TEXT,
        created_at      TEXT NOT NULL,
        created_by      TEXT,
        tenant_id       TEXT,
        classification  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dic_chunk_links (
        link_id         TEXT PRIMARY KEY,
        doc_id          TEXT NOT NULL,
        version_id      TEXT NOT NULL,
        rag_chunk_id    TEXT NOT NULL,
        collection_id   TEXT,
        chunk_index     INTEGER NOT NULL,
        page            INTEGER,
        section         TEXT,
        created_at      TEXT NOT NULL,
        tenant_id       TEXT,
        classification  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dic_community_summaries (
        summary_id      TEXT PRIMARY KEY,
        community_id    TEXT NOT NULL,
        summary_text    TEXT NOT NULL,
        citations_list  TEXT DEFAULT '[]',
        model_version   TEXT DEFAULT '',
        created_at      TEXT DEFAULT (datetime('now')),
        updated_at      TEXT DEFAULT (datetime('now')),
        tenant_id       TEXT DEFAULT 'default',
        classification  TEXT DEFAULT 'CUI'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rag_chunks (
        id           TEXT PRIMARY KEY,
        content      TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        source_type  TEXT NOT NULL DEFAULT 'dic_document',
        source_id    TEXT NOT NULL DEFAULT '',
        chunk_index  INTEGER DEFAULT 0,
        metadata     TEXT DEFAULT '{}',
        tenant_id    TEXT DEFAULT 'default',
        classification TEXT DEFAULT 'CUI',
        created_at   TEXT DEFAULT (datetime('now'))
    )
    """,
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _chunk_id(doc_id: str) -> str:
    return f"{doc_id}_chunk_0"


def _version_id(doc_id: str) -> str:
    return f"{doc_id}_v1"


def _all_doc_ids() -> list[str]:
    return [d["doc_id"] for d in _CORPUS_DOCS]


def _all_community_ids() -> list[str]:
    return sorted({d["community_id"] for d in _CORPUS_DOCS})


# --------------------------------------------------------------------------- #
# Seed / purge
# --------------------------------------------------------------------------- #

def _ensure_schema(conn) -> None:
    cur = conn.cursor()
    for ddl in _SCHEMA_DDL:
        cur.execute(ddl)
    conn.commit()


def purge_corpus(conn) -> None:
    """Remove every row this module seeds. Idempotent; safe to call repeatedly.

    Scoped strictly to the test collection / deterministic ids so it can never
    touch real DIC data sharing the same database file.
    """
    doc_ids = _all_doc_ids()
    community_ids = _all_community_ids()
    chunk_ids = [_chunk_id(d) for d in doc_ids]
    version_ids = [_version_id(d) for d in doc_ids]
    cur = conn.cursor()

    def _del(sql: str, params: list) -> None:
        if not params:
            return
        marks = ",".join("?" for _ in params)
        try:
            cur.execute(sql.format(marks=marks), params)
        except Exception:
            # Table may not exist yet on a bare DB — purge is best-effort.
            pass

    _del("DELETE FROM dic_chunk_links WHERE doc_id IN ({marks})", doc_ids)
    _del("DELETE FROM dic_versions WHERE version_id IN ({marks})", version_ids)
    _del("DELETE FROM dic_documents WHERE doc_id IN ({marks})", doc_ids)
    _del("DELETE FROM rag_chunks WHERE id IN ({marks})", chunk_ids)
    _del("DELETE FROM dic_community_summaries WHERE community_id IN ({marks})", community_ids)
    _del("DELETE FROM dic_collections WHERE collection_id IN ({marks})", [CORPUS_COLLECTION_ID])
    conn.commit()


def seed_corpus(conn) -> SeededCorpus:
    """Seed the deterministic corpus and return its :class:`SeededCorpus`.

    Ensures the schema, purges any prior copy (so reruns are identical), then
    inserts the collection, documents, approved versions, chunk text, and
    chunk→document links. The community index (``dic_community_summaries``) is
    deliberately left **empty** — populating it is the precompute step's job.
    """
    _ensure_schema(conn)
    purge_corpus(conn)

    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO dic_collections
            (collection_id, name, description, owner_id, retention_days,
             classification, tenant_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            CORPUS_COLLECTION_ID, "GraphRAG Community Test Corpus",
            "Deterministic corpus for dic-adapt-07-d4 community tests",
            "test", 90, CORPUS_CLASSIFICATION, CORPUS_TENANT_ID, _SEED_TS,
        ),
    )

    documents: list[dict] = []
    chunk_ids: list[str] = []
    communities: dict[str, list[str]] = {}

    for idx, doc in enumerate(_CORPUS_DOCS):
        doc_id = doc["doc_id"]
        content = doc["content"]
        content_hash = _sha256(content)
        version_id = _version_id(doc_id)
        chunk_id = _chunk_id(doc_id)

        cur.execute(
            """
            INSERT INTO dic_documents
                (doc_id, collection_id, source_id, filename, filepath,
                 content_type, provider, title, byte_size, content_sha256,
                 page_count, created_at, tenant_id, classification)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id, CORPUS_COLLECTION_ID, doc_id, f"{doc_id}.txt",
                f"/corpus/{doc_id}.txt", "text/plain", "seed", doc["title"],
                len(content.encode("utf-8")), content_hash, 1, _SEED_TS,
                CORPUS_TENANT_ID, CORPUS_CLASSIFICATION,
            ),
        )
        cur.execute(
            """
            INSERT INTO dic_versions
                (version_id, doc_id, version_no, origin, status,
                 content_sha256, created_at, created_by, tenant_id, classification)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id, doc_id, 1, "human_authored", "approved",
                content_hash, _SEED_TS, "test", CORPUS_TENANT_ID,
                CORPUS_CLASSIFICATION,
            ),
        )
        cur.execute(
            """
            INSERT INTO rag_chunks
                (id, content, content_hash, source_type, source_id,
                 chunk_index, metadata, tenant_id, classification, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk_id, content, content_hash, "dic_document", doc_id, 0,
                json.dumps({"community_id": doc["community_id"], "doc_id": doc_id}),
                CORPUS_TENANT_ID, CORPUS_CLASSIFICATION, _SEED_TS,
            ),
        )
        cur.execute(
            """
            INSERT INTO dic_chunk_links
                (link_id, doc_id, version_id, rag_chunk_id, collection_id,
                 chunk_index, page, section, created_at, tenant_id, classification)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{version_id}_link_0", doc_id, version_id, chunk_id,
                CORPUS_COLLECTION_ID, 0, 1, doc["title"], _SEED_TS,
                CORPUS_TENANT_ID, CORPUS_CLASSIFICATION,
            ),
        )

        documents.append({**doc, "chunk_id": chunk_id, "version_id": version_id})
        chunk_ids.append(chunk_id)
        communities.setdefault(doc["community_id"], []).append(doc_id)

    conn.commit()

    return SeededCorpus(
        collection_id=CORPUS_COLLECTION_ID,
        tenant_id=CORPUS_TENANT_ID,
        classification=CORPUS_CLASSIFICATION,
        documents=documents,
        doc_ids=_all_doc_ids(),
        chunk_ids=chunk_ids,
        communities=communities,
    )


# --------------------------------------------------------------------------- #
# Precompute — the GraphRAG community computation pipeline (-d2)
# --------------------------------------------------------------------------- #

#: Stable model tag recorded on every precomputed summary. NO-LLM/grounded:
#: the summary is a deterministic, extractive join of the member chunk text,
#: so the "model" is the pipeline version itself — no Claude/Ollama tokens.
PRECOMPUTE_MODEL_VERSION = "dic-graphrag-grounded-v1"


def _summary_id(community_id: str) -> str:
    return f"{community_id}_summary"


def _read_community_members(conn) -> dict[str, list[dict]]:
    """Recover community → member-chunk grouping *from the seeded store*.

    Walks ``dic_chunk_links`` → ``rag_chunks`` for the test collection and
    groups by the ``community_id`` carried in each chunk's metadata. This reads
    the persisted graph (not the :class:`SeededCorpus` contract) so the
    precompute genuinely recovers the clustering the seed encoded.

    Returns a mapping ``community_id`` → list of member dicts
    (``doc_id``, ``chunk_id``, ``content``), each list ordered deterministically
    by ``doc_id`` so summaries and citations are reproducible.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT cl.doc_id        AS doc_id,
               cl.rag_chunk_id  AS chunk_id,
               rc.content       AS content,
               rc.metadata      AS metadata
        FROM dic_chunk_links cl
        JOIN rag_chunks rc ON rc.id = cl.rag_chunk_id
        WHERE cl.collection_id = ?
        ORDER BY cl.doc_id, cl.chunk_index
        """,
        (CORPUS_COLLECTION_ID,),
    )
    members: dict[str, list[dict]] = {}
    for row in cur.fetchall():
        doc_id, chunk_id, content, metadata = (
            row[0], row[1], row[2], row[3],
        )
        try:
            meta = json.loads(metadata) if metadata else {}
        except (TypeError, ValueError):
            meta = {}
        community_id = meta.get("community_id")
        if not community_id:
            continue
        members.setdefault(community_id, []).append(
            {"doc_id": doc_id, "chunk_id": chunk_id, "content": content}
        )
    for community_id in members:
        members[community_id].sort(key=lambda m: m["doc_id"])
    return members


def precompute_communities(conn, *, model_version: str = PRECOMPUTE_MODEL_VERSION) -> dict[str, dict]:
    """Run the community-summary precompute over the seeded corpus.

    The GraphRAG "graph computation" step: cluster the corpus by community,
    then write exactly one grounded summary per community into
    ``dic_community_summaries``. The summary text is a deterministic,
    extractive concatenation of the member chunk contents (NO-LLM), and the
    citation list names every contributing document + chunk so every claim is
    traceable. Idempotent — re-running replaces the summaries for the same
    communities rather than appending duplicates.

    Returns a mapping ``community_id`` → ``{"summary_text", "citations",
    "doc_ids"}`` describing what was written, so callers can assert against it
    without re-reading the table.
    """
    members = _read_community_members(conn)
    cur = conn.cursor()
    written: dict[str, dict] = {}

    for community_id in sorted(members):
        community_members = members[community_id]
        # Grounded summary: join member chunk text verbatim, in doc_id order.
        summary_text = " ".join(m["content"].strip() for m in community_members).strip()
        # Citations: one entry per contributing chunk, traceable to its document.
        citations = [
            {"doc_id": m["doc_id"], "chunk_id": m["chunk_id"]}
            for m in community_members
        ]
        # Idempotent upsert: clear any prior summary for this community first.
        cur.execute(
            "DELETE FROM dic_community_summaries WHERE community_id = ?",
            (community_id,),
        )
        cur.execute(
            """
            INSERT INTO dic_community_summaries
                (summary_id, community_id, summary_text, citations_list,
                 model_version, created_at, updated_at, tenant_id, classification)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _summary_id(community_id), community_id, summary_text,
                json.dumps(citations), model_version, _SEED_TS, _SEED_TS,
                CORPUS_TENANT_ID, CORPUS_CLASSIFICATION,
            ),
        )
        written[community_id] = {
            "summary_text": summary_text,
            "citations": citations,
            "doc_ids": [m["doc_id"] for m in community_members],
        }

    conn.commit()
    return written


# --------------------------------------------------------------------------- #
# Fixture
# --------------------------------------------------------------------------- #

@pytest.fixture
def setup_corpus():
    """Seed the deterministic GraphRAG corpus before a test; purge it after.

    Yields a :class:`SeededCorpus`. The downstream precompute (``-d2``) and
    corpus-level query (``-d3``) tests request this fixture so they all operate
    over an identical, reproducible graph.
    """
    conn = get_connection()
    try:
        corpus = seed_corpus(conn)
    finally:
        conn.close()

    yield corpus

    cleanup = get_connection()
    try:
        purge_corpus(cleanup)
    finally:
        cleanup.close()


# --------------------------------------------------------------------------- #
# Smoke test for this task's deliverable (the fixture itself)
# --------------------------------------------------------------------------- #

def test_setup_corpus_seeds_deterministic_corpus(setup_corpus):
    """The fixture seeds exactly the expected documents, chunks, and clusters."""
    corpus = setup_corpus

    # Contract shape: two communities of two documents each.
    assert corpus.collection_id == CORPUS_COLLECTION_ID
    assert corpus.doc_count == 4
    assert corpus.community_ids == ["comm_budget", "comm_security"]
    assert all(len(members) == 2 for members in corpus.communities.values())
    # Every document maps into exactly one community.
    grouped = [d for members in corpus.communities.values() for d in members]
    assert sorted(grouped) == sorted(corpus.doc_ids)

    # Rows actually landed in the document store + chunk store.
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM dic_documents WHERE collection_id = ?",
            (CORPUS_COLLECTION_ID,),
        )
        assert cur.fetchone()[0] == 4
        marks = ",".join("?" for _ in corpus.chunk_ids)
        cur.execute(
            f"SELECT COUNT(*) FROM rag_chunks WHERE id IN ({marks})",
            corpus.chunk_ids,
        )
        assert cur.fetchone()[0] == 4
        # The community index starts empty — precompute (-d2) fills it.
        cur.execute(
            "SELECT COUNT(*) FROM dic_community_summaries WHERE community_id IN (?, ?)",
            tuple(corpus.community_ids),
        )
        assert cur.fetchone()[0] == 0
    finally:
        conn.close()


def test_setup_corpus_is_idempotent(setup_corpus):
    """Re-seeding over an existing corpus yields the same row counts (no dupes)."""
    corpus = setup_corpus
    conn = get_connection()
    try:
        again = seed_corpus(conn)
        assert again.doc_ids == corpus.doc_ids
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM dic_documents WHERE collection_id = ?",
            (CORPUS_COLLECTION_ID,),
        )
        assert cur.fetchone()[0] == 4
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Precompute step (-d2): run the graph computation pipeline post-seed
# --------------------------------------------------------------------------- #

def test_seed_and_precompute(setup_corpus):
    """Trigger the community precompute right after seeding and verify the
    community structures are ready for corpus-level querying (-d3).

    Asserts the pipeline:
      * recovers exactly the corpus's communities from the persisted store,
      * writes exactly one summary row per community (no dupes / no extras),
      * grounds each summary in its member documents' actual text, and
      * cites every contributing document so answers stay traceable.
    """
    corpus = setup_corpus

    conn = get_connection()
    try:
        # Precondition: the seed leaves the community index empty.
        cur = conn.cursor()
        marks = ",".join("?" for _ in corpus.community_ids)
        cur.execute(
            f"SELECT COUNT(*) FROM dic_community_summaries WHERE community_id IN ({marks})",
            corpus.community_ids,
        )
        assert cur.fetchone()[0] == 0, "precompute must start from an empty index"

        # Trigger the graph computation pipeline.
        written = precompute_communities(conn)

        # The pipeline recovered exactly the seeded communities.
        assert sorted(written) == corpus.community_ids

        # Exactly one summary row landed per community — structures are ready.
        cur.execute(
            f"""
            SELECT community_id, summary_text, citations_list, model_version
            FROM dic_community_summaries
            WHERE community_id IN ({marks})
            ORDER BY community_id
            """,
            corpus.community_ids,
        )
        rows = cur.fetchall()
        assert len(rows) == len(corpus.community_ids)

        # Map seed doc_id → its source content for grounding checks.
        content_by_doc = {d["doc_id"]: d["content"] for d in corpus.documents}

        for community_id, summary_text, citations_json, model_version in (
            (r[0], r[1], r[2], r[3]) for r in rows
        ):
            expected_docs = sorted(corpus.communities[community_id])
            # Grounded, non-empty summary tagged with the pipeline version.
            assert summary_text.strip(), f"{community_id} summary is empty"
            assert model_version == PRECOMPUTE_MODEL_VERSION

            # Citations cover every member document exactly once, no strays.
            citations = json.loads(citations_json)
            cited_docs = sorted(c["doc_id"] for c in citations)
            assert cited_docs == expected_docs
            assert all(c.get("chunk_id") for c in citations), "citations need chunk ids"

            # Every member document's text is actually present in its summary —
            # this is what makes the summary grounded rather than hallucinated.
            for doc_id in expected_docs:
                snippet = content_by_doc[doc_id].strip()
                assert snippet in summary_text, (
                    f"{community_id} summary is not grounded in {doc_id}"
                )
    finally:
        conn.close()


def test_precompute_is_idempotent(setup_corpus):
    """Re-running the precompute replaces, never duplicates, community summaries."""
    corpus = setup_corpus
    conn = get_connection()
    try:
        first = precompute_communities(conn)
        second = precompute_communities(conn)
        assert first == second  # deterministic, NO-LLM output

        cur = conn.cursor()
        marks = ",".join("?" for _ in corpus.community_ids)
        cur.execute(
            f"SELECT COUNT(*) FROM dic_community_summaries WHERE community_id IN ({marks})",
            corpus.community_ids,
        )
        # Still exactly one row per community after two runs.
        assert cur.fetchone()[0] == len(corpus.community_ids)
    finally:
        conn.close()
