
"""
DataOps Mission 1: Advanced RAG — Chunking, Embedding, Reranking
Goal: Build a production-grade RAG pipeline with sliding-window chunking,
      cosine similarity search, and a reranking filter.
"""

import math
import re
from collections import Counter

# ── Document Corpus ───────────────────────────────────────────────────────────

COMPLIANCE_CORPUS = [
    {
        "id": "fedramp-high",
        "source": "FedRAMP High Baseline",
        "text": (
            "FedRAMP High is designed for cloud services that process highly sensitive "
            "government data. Systems at this level must implement 421 security controls. "
            "Multi-factor authentication is mandatory for all privileged access under "
            "controls IA-2 and IA-5. Continuous monitoring requires monthly vulnerability "
            "scans and annual penetration testing. Systems must maintain an authorization "
            "boundary and document all data flows crossing that boundary."
        ),
    },
    {
        "id": "stig-rhel8",
        "source": "RHEL 8 STIG",
        "text": (
            "The RHEL 8 STIG contains 248 checks for hardening Red Hat Enterprise Linux 8. "
            "CAT I findings (critical) include: SSH PermitRootLogin must be disabled "
            "(V-220706), FIPS 140-2 mode must be enabled (V-230223), and inactive accounts "
            "must be locked after 35 days. CAT II findings require remediation within 90 "
            "days per DoD POA&M policy. Automated STIG compliance can be achieved using "
            "the DISA RHEL 8 STIG Ansible role available on GitHub."
        ),
    },
    {
        "id": "cmmc-l2",
        "source": "CMMC Level 2",
        "text": (
            "CMMC Level 2 encompasses 110 practices derived from NIST SP 800-171. "
            "Organizations handling Controlled Unclassified Information (CUI) must achieve "
            "Level 2 certification by 2026 to bid on DoD contracts. Third-party assessments "
            "are required for contracts involving critical national security programs. "
            "Level 2 includes access control (AC), identification and authentication (IA), "
            "incident response (IR), and system and communications protection (SC) domains."
        ),
    },
    {
        "id": "nist-csf",
        "source": "NIST Cybersecurity Framework 2.0",
        "text": (
            "The NIST CSF 2.0 introduces a new Govern function alongside the existing "
            "Identify, Protect, Detect, Respond, and Recover functions. The Govern function "
            "addresses organizational context, risk management strategy, cybersecurity supply "
            "chain risk management, and oversight. Organizations are encouraged to use "
            "Community Profiles to share implementation guidance across sectors. The framework "
            "now explicitly addresses AI system risks and supply chain security."
        ),
    },
]


# ── Utility: Simple TF Embedding (no external deps) ──────────────────────────

def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text.lower())


def _embed(text: str) -> dict[str, float]:
    tokens = _tokenize(text)
    if not tokens:
        return {}
    counts = Counter(tokens)
    total = len(tokens)
    return {tok: cnt / total for tok, cnt in counts.items()}


def _cosine(a: dict, b: dict) -> float:
    keys = set(a) & set(b)
    if not keys:
        return 0.0
    dot = sum(a[k] * b[k] for k in keys)
    mag_a = math.sqrt(sum(v * v for v in a.values()))
    mag_b = math.sqrt(sum(v * v for v in b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# ── Step 1: Chunker ───────────────────────────────────────────────────────────

def chunk_document(text: str, chunk_size: int = 200, overlap: int = 50) -> list[str]:
    """TODO: Split text into overlapping chunks.

    Use a sliding window:
    - Start at position 0
    - Take chunk_size characters
    - Advance by (chunk_size - overlap) characters for the next chunk
    - Stop when the start position exceeds len(text)

    Return a list of chunk strings. Skip empty chunks.
    """
    # YOUR CODE HERE
    pass


# ── Step 2: Index Builder ─────────────────────────────────────────────────────

def build_index(docs: list[dict], chunk_size: int = 200, overlap: int = 50) -> list[dict]:
    """TODO: Build a searchable index from documents.

    For each doc in docs:
      1. Call chunk_document(doc["text"], chunk_size, overlap)
      2. For each chunk, create an entry:
         {"text": chunk, "embedding": _embed(chunk), "source": doc["source"], "doc_id": doc["id"]}
      3. Skip empty chunks

    Return a flat list of all chunk entries across all docs.
    """
    # YOUR CODE HERE
    pass


# ── Step 3: Semantic Search ───────────────────────────────────────────────────

def semantic_search(query: str, index: list[dict], top_k: int = 3) -> list[dict]:
    """TODO: Find the top_k most relevant chunks using cosine similarity.

    1. Compute the query embedding: _embed(query)
    2. For each entry in index, compute _cosine(query_emb, entry["embedding"])
    3. Sort entries by descending cosine score
    4. Return the top_k entries, each with a "score" field added

    Return: [{"text": ..., "source": ..., "score": float, ...}, ...]
    """
    # YOUR CODE HERE
    pass


# ── Step 4: Reranker ──────────────────────────────────────────────────────────

def rerank(query: str, candidates: list[dict], threshold: float = 0.05) -> list[dict]:
    """TODO: Filter candidates that are below the relevance threshold.

    1. Tokenize the query into a set of words (use _tokenize)
    2. For each candidate, count how many query tokens appear in the candidate text
    3. Compute a keyword_overlap_score = matching_tokens / len(query_tokens)
    4. Keep candidates where keyword_overlap_score >= threshold
    5. Sort the kept candidates by their existing "score" (from semantic_search) descending

    Return the filtered, sorted list.
    """
    # YOUR CODE HERE
    pass


# ── Step 5: Advanced RAG ─────────────────────────────────────────────────────

class AdvancedRAG:
    """Production-grade RAG: chunking + semantic search + reranking."""

    def __init__(self, docs: list[dict], chunk_size: int = 200, overlap: int = 50):
        self.index = build_index(docs, chunk_size, overlap)

    def query(self, question: str, top_k: int = 3) -> str:
        """TODO: Answer a question using the RAG pipeline.

        1. semantic_search(question, self.index, top_k=top_k * 2) — over-retrieve
        2. rerank(question, candidates) — filter to most relevant
        3. Take the top top_k results after reranking
        4. Build a context string: for each result, "Source: {source}\n{text}\n"
        5. Return: "Based on: {comma-separated sources}\n\n{first_result_text}"

        The answer MUST reference at least one source document name.
        """
        # YOUR CODE HERE
        pass


# Test
if __name__ == "__main__":
    rag = AdvancedRAG(COMPLIANCE_CORPUS, chunk_size=200, overlap=50)

    questions = [
        "What are the MFA requirements for FedRAMP High?",
        "How long do I have to remediate a CAT I STIG finding?",
        "What is CMMC Level 2 and who needs it?",
    ]

    for q in questions:
        answer = rag.query(q)
        print(f"Q: {q}")
        print(f"A: {answer[:120]}")
        print()
