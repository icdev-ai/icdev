"""
Tier 2 DataOps Mission 3: Corrective RAG (CRAG)
Goal: Build a Corrective RAG pipeline that validates retrieved documents
      before using them, and falls back to a broader search when quality is low.

Standard RAG retrieves and uses — CRAG retrieves, validates, then decides:
  HIGH confidence → use retrieved docs directly
  LOW confidence  → filter bad docs, supplement with fallback corpus
  NO valid docs   → use fallback corpus entirely
"""

# ── Simulated Document Corpora ────────────────────────────────────────────────

PRIMARY_CORPUS = [
    {"id": "p1", "text": "NIST 800-53 IA-2 requires multi-factor authentication for privileged accounts.",
     "source": "NIST 800-53 Rev 5", "relevance_hint": "IA-2 MFA privileged"},
    {"id": "p2", "text": "Account management procedures must include formal request and approval processes.",
     "source": "NIST 800-53 Rev 5", "relevance_hint": "AC-2 account management"},
    {"id": "p3", "text": "Python is a high-level programming language used in data science applications.",
     "source": "Wikipedia", "relevance_hint": "unrelated programming topic"},
    {"id": "p4", "text": "Audit events must be logged for account creation, modification, and deletion.",
     "source": "NIST 800-53 Rev 5", "relevance_hint": "AU-2 audit logging accounts"},
    {"id": "p5", "text": "The capital of France is Paris, located in Western Europe.",
     "source": "Geography", "relevance_hint": "unrelated geography topic"},
]

FALLBACK_CORPUS = [
    {"id": "f1", "text": "All federal information systems must implement FIPS 140-2 validated cryptography.",
     "source": "FIPS 140-2", "relevance_hint": "cryptography federal"},
    {"id": "f2", "text": "Continuous monitoring requires automated collection of security metrics.",
     "source": "NIST 800-137", "relevance_hint": "continuous monitoring security"},
]

QUERY_KEYWORDS = {
    "IA-2 authentication": ["mfa", "multi-factor", "authentication", "privileged", "ia-2"],
    "AC-2 account": ["account", "management", "ac-2", "request", "approval"],
    "audit logging": ["audit", "log", "logging", "au-2", "event"],
}

RELEVANCE_THRESHOLD = 0.3


# ── Step 1: Document Relevance Scorer ────────────────────────────────────────

def score_relevance(doc: dict, query: str) -> float:
    """TODO: Score a document's relevance to a query (0.0 to 1.0).

    Simple keyword overlap scoring:
    1. Tokenize query to lowercase words (split on spaces)
    2. Tokenize doc["text"] to lowercase words
    3. Count matching words (intersection size)
    4. Score = matching_words / max(len(query_words), 1)
    5. Cap at 1.0

    Return float 0.0 to 1.0.
    """
    # YOUR CODE HERE
    pass


# ── Step 2: Document Validator ────────────────────────────────────────────────

def validate_documents(docs: list[dict], query: str, threshold: float = RELEVANCE_THRESHOLD) -> tuple[list[dict], list[dict]]:
    """TODO: Split documents into valid (above threshold) and invalid (below).

    For each doc:
    - Compute score_relevance(doc, query)
    - Add {"relevance_score": score} to the doc dict (mutate in place)
    - If score >= threshold → valid
    - If score < threshold → invalid

    Return (valid_docs, invalid_docs) — two separate lists.
    """
    # YOUR CODE HERE
    pass


# ── Step 3: CorrectiveRAG ─────────────────────────────────────────────────────

class CorrectiveRAG:
    """Corrective RAG pipeline with document validation and fallback."""

    CONFIDENCE_HIGH = 0.5   # avg relevance score for "high confidence" retrieval
    CONFIDENCE_LOW = 0.2    # below this → no valid docs, use fallback only

    def retrieve(self, query: str, corpus: list[dict] = PRIMARY_CORPUS) -> list[dict]:
        """TODO: Retrieve top-3 most relevant documents from corpus.

        Score all docs, sort by relevance_score descending, return top 3.
        Use score_relevance() for scoring (don't mutate corpus docs — work on copies).
        Return list of up to 3 dicts with relevance_score added.
        """
        # YOUR CODE HERE
        pass

    def query(self, query: str) -> dict:
        """TODO: Run the full CRAG pipeline for a query.

        Steps:
        1. retrieve(query, PRIMARY_CORPUS) → retrieved_docs
        2. validate_documents(retrieved_docs, query) → valid_docs, invalid_docs
        3. Compute avg_relevance = mean of valid_docs relevance scores (0.0 if none)
        4. Decision:
           - If avg_relevance >= CONFIDENCE_HIGH → "high": use valid_docs as final_docs
           - If avg_relevance >= CONFIDENCE_LOW  → "low": use valid_docs + retrieve(query, FALLBACK_CORPUS)
           - Else                                → "none": use retrieve(query, FALLBACK_CORPUS) only
        5. Return:
        {
            "query": query,
            "confidence": "high" | "low" | "none",
            "retrieved_count": len(retrieved_docs),
            "valid_count": len(valid_docs),
            "final_docs": final_docs,
            "used_fallback": confidence != "high",
        }
        """
        # YOUR CODE HERE
        pass


# Test
if __name__ == "__main__":
    crag = CorrectiveRAG()

    queries = [
        "IA-2 authentication requirements for privileged users",
        "What is the audit logging policy for account events",
        "What is the best pizza restaurant in Rome Italy",
    ]

    for q in queries:
        result = crag.query(q)
        print(f"\nQuery: {q[:50]}...")
        print(f"  Confidence: {result['confidence']}")
        print(f"  Retrieved: {result['retrieved_count']}, Valid: {result['valid_count']}")
        print(f"  Used fallback: {result['used_fallback']}")
        print(f"  Final docs: {[d['id'] for d in result['final_docs']]}")
