<!-- CUI // SP-CTI -->

# Configure Corpus Validation with quality_feedback_loop.py

The threat model is clear. Now wire the validation pipeline into your RAG ingestion workflow. This step covers the full `quality_feedback_loop.py` API, the quarantine workflow, the four-stage validation pipeline, and a scheduled daily validation job.

## `run_feedback_cycle()` Return Value

```python
from tools.rag.quality_feedback_loop import run_feedback_cycle

results = run_feedback_cycle(
    corpus_id="contract-corpus",
    quality_threshold=0.7,    # documents below this score are flagged
)

# Returns:
# {
#   "corpus_id": "contract-corpus",
#   "total_docs": 1847,
#   "passed": 1821,
#   "failed": 14,
#   "quarantined": 12,
#   "health_score": 0.987,
#   "anomalies": [
#     {
#       "doc_id": "doc_0042f7",
#       "anomaly_type": "topic_drift",
#       "severity": "warning",
#       "recommendation": "Review document source and topic alignment"
#     },
#     {
#       "doc_id": "doc_1193a2",
#       "anomaly_type": "source_anomaly",
#       "severity": "critical",
#       "recommendation": "Document origin does not match expected domain"
#     }
#   ]
# }
```

## Anomaly Types

| Anomaly Type | What It Means | Default Action |
|---|---|---|
| `topic_drift` | Document topic diverges significantly from corpus centroid | Flag for review |
| `quality_drop` | Document quality score below threshold | Flag for review |
| `source_anomaly` | Document origin doesn't match expected domains | Quarantine immediately |
| `duplicate` | Document is a near-copy of an existing document | Deduplicate |

`source_anomaly` is the highest-risk anomaly type — it indicates a document from an unexpected source was ingested, which could mean an injection attack or a misconfigured ingestion pipeline.

## The Four-Stage Validation Pipeline

Each document passes through four validation stages before being admitted to the production corpus:

### Stage 1: Source Validation

Check the origin of the document before processing its content:

```python
ALLOWED_DOMAINS = frozenset([
    "acquisition.gov",
    "sam.gov",
    "regulations.gov",
    "your-sharepoint-domain.sharepoint.com",
])

def validate_source(doc_metadata: dict) -> bool:
    origin = doc_metadata.get("source_url", "")
    domain = origin.split("/")[2] if "://" in origin else "unknown"
    return domain in ALLOWED_DOMAINS
```

### Stage 2: Content Quality Scoring

Coherence (does the document make sense internally?), completeness (does it have a meaningful body?), and formatting (is it structured content or garbage bytes?).

```python
from tools.rag.quality_feedback_loop import score_document_quality

quality = score_document_quality(doc_id="doc_0042f7")
# Returns: {"coherence": 0.81, "completeness": 0.94, "format_score": 0.88, "composite": 0.87}
```

### Stage 3: Semantic Consistency Check

Compute cosine similarity between the document embedding and the corpus centroid. Documents far from the centroid are topic outliers:

```python
import numpy as np
from tools.rag.embedder import get_embedding

corpus_centroid = np.load(f"data/corpora/{corpus_id}/centroid.npy")
doc_embedding = get_embedding(document_text)
similarity = float(np.dot(doc_embedding, corpus_centroid) /
                   (np.linalg.norm(doc_embedding) * np.linalg.norm(corpus_centroid)))

if similarity < 0.45:
    flag_as_topic_drift(doc_id)
```

### Stage 4: Freshness Check

Reject documents past their TTL to prevent stale data from corrupting retrieval quality:

```python
from datetime import datetime, timezone, timedelta

def check_freshness(doc_metadata: dict, ttl_days: int = 365) -> bool:
    published_at = datetime.fromisoformat(doc_metadata.get("published_at", "2000-01-01"))
    age = datetime.now(timezone.utc) - published_at.replace(tzinfo=timezone.utc)
    return age < timedelta(days=ttl_days)
```

## Scheduled Daily Validation Job

```python
# tools/rag/validation_scheduler.py
# Run via cron: 0 2 * * * python tools/rag/validation_scheduler.py

from tools.rag.quality_feedback_loop import run_feedback_cycle, get_feedback_status
from tools.memory.memory_write import write_memory
import json
from datetime import datetime, timezone

CORPORA = ["contract-corpus", "policy-corpus", "technical-docs"]

def daily_validation():
    for corpus_id in CORPORA:
        results = run_feedback_cycle(corpus_id=corpus_id, quality_threshold=0.7)
        # Alert if corpus health drops below 0.95
        if results["health_score"] < 0.95:
            write_memory(
                content=f"[ALERT] Corpus {corpus_id} health: {results['health_score']:.3f}. "
                        f"{results['quarantined']} docs quarantined.",
                memory_type="event",
            )
        # Log results
        print(json.dumps({"corpus": corpus_id, **results}, indent=2))

if __name__ == "__main__":
    daily_validation()
```

## Quarantine Workflow

Documents that fail validation enter the quarantine bucket — they are removed from retrieval but not deleted:

```
Failed validation → quarantine_docs table → human review queue
     ↓                                              ↓
(removed from                           Reviewer approves → restore to corpus
 retrieval index)                       Reviewer rejects → permanently exclude
                                        No action in 30d → auto-exclude
```

**Your task:** Answer the configuration questions.
