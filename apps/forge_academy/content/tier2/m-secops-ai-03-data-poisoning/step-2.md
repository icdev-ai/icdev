---
ontology_id: icdev:mission:m-secops-ai-03-data-poisoning:step:2
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Configure Corpus Validation with quality_feedback_loop.py

The threat model is clear. Now design the validation pipeline around your RAG corpus. This step covers what `quality_feedback_loop.py` really returns, the per-document checks you add at ingestion, and a scheduled job that ties them together. Answer the configuration questions at the end.

## What `run_feedback_cycle()` Returns

`run_feedback_cycle()` works on the platform's RAG quality metrics, not on one named corpus. It takes `dry_run` and an optional DB connection:

```python
from tools.rag.quality_feedback_loop import run_feedback_cycle, get_feedback_status

result = run_feedback_cycle(dry_run=True)   # check quality only; don't generate training pairs

# result (shape):
# {
#   "cycle_id": "cycle-...",
#   "dry_run": True,
#   "quality_status": "ok",            # or "disabled" when the monitor is off
#   "metrics": {"avg_retrieval_score": 0.71, "query_count": 412, "ndcg": 0.58, "mrr": 0.47},
#   "alerts": [...],                    # metrics below their configured thresholds
#   "retrain_recommended": False,
#   "anomaly_detection": {"anomalous": False, "reasons": [], "floors": {...}},
#   "actions": ["no_action_needed"],
# }

status = get_feedback_status()            # current quality, anomaly verdict, snapshot history
```

The thresholds live in `args/finetune_config.yaml` under `quality_feedback:` (for example `min_ndcg: 0.5`, `min_mrr: 0.4`). The metrics come from `rag_retrieval_log` and `rag_evaluations` over the last 7 days. A sharp drop in `avg_retrieval_score` or `ndcg` after a big ingestion batch is the corpus-level poisoning signal.

## The Four Ingestion Checks (you build these)

The feedback loop tells you *that* quality moved, not *which* document moved it. Per-document validation happens before a document reaches the index. The four checks below are a design you implement in your ingestion path. They are not an ICDEV API.

### Check 1: Source Validation

Check where a document came from before processing its content:

```python
from urllib.parse import urlparse

ALLOWED_DOMAINS = frozenset([
    "acquisition.gov",
    "sam.gov",
    "regulations.gov",
    "your-sharepoint-domain.sharepoint.com",
])

def validate_source(doc_metadata: dict) -> bool:
    host = urlparse(doc_metadata.get("source_url", "")).hostname or ""
    return host in ALLOWED_DOMAINS
```

### Check 2: Embedded-Instruction Scan

Run ICDEV's injection detector over the document text. This catches Type 4 attacks (instructions addressed to the LLM) before they reach the index:

```python
from tools.security.prompt_injection_detector import PromptInjectionDetector

_detector = PromptInjectionDetector()

def scan_document(text: str, doc_id: str) -> bool:
    verdict = _detector.scan_text(text, source=f"rag_ingest:{doc_id}")
    return verdict["action"] not in ("block", "flag")   # False -> quarantine
```

### Check 3: Semantic Consistency (design pattern)

Compare the document's embedding with the corpus centroid. Documents far from the centroid are topic outliers worth a human look. Get embeddings from your configured embedding provider. ICDEV does not ship a corpus-centroid file.

```python
import math

def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))

def is_topic_outlier(doc_embedding, corpus_centroid, floor: float = 0.45) -> bool:
    return cosine(doc_embedding, corpus_centroid) < floor
```

### Check 4: Freshness

Reject documents past their time-to-live (TTL) so stale data cannot crowd out current sources:

```python
from datetime import datetime, timezone, timedelta

def check_freshness(doc_metadata: dict, ttl_days: int = 365) -> bool:
    published_at = datetime.fromisoformat(doc_metadata.get("published_at", "2000-01-01"))
    age = datetime.now(timezone.utc) - published_at.replace(tzinfo=timezone.utc)
    return age < timedelta(days=ttl_days)
```

## Scheduled Validation Job

A small job that runs the corpus-level signal on a schedule and records an alert in ICDEV memory when it trips:

```python
# my_corpus_watch.py  (yours. Schedule it with cron or Task Scheduler)
import json
from tools.rag.quality_feedback_loop import run_feedback_cycle
from tools.memory.memory_write import write_to_db

def nightly_check():
    result = run_feedback_cycle(dry_run=True)
    anomaly = result.get("anomaly_detection", {})
    if anomaly.get("anomalous") or result.get("retrain_recommended"):
        write_to_db(
            "[ALERT] RAG quality regression: " + "; ".join(anomaly.get("reasons", [])),
            entry_type="event",
        )
    print(json.dumps(result, indent=2, default=str))

if __name__ == "__main__":
    nightly_check()
```

## Quarantine Workflow

ICDEV has no quarantine table for RAG documents. This is the workflow you would build. Documents that fail a check are held out of the index, not deleted:

```
Failed check → your quarantine store → human review queue
     ↓                                          ↓
(never indexed)                     Reviewer approves → ingest
                                    Reviewer rejects → permanently exclude
                                    No action in 30d → auto-exclude
```

**Your task:** Answer the configuration questions below, then click **Configure →**. "Corpus ID" is whatever name your ingestion path uses for the collection, and "quality threshold" is the floor you would set for the corpus-level signal.
