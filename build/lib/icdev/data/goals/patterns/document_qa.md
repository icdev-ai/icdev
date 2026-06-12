# Goal: Document Q&A Assistant
# CUI // SP-CTI

## Purpose
Enable non-AI teams to deploy a natural-language Q&A interface over policy documents, SOPs, and reference materials — without writing code.

## Inputs
- `$DOCUMENT_SOURCES` — list of PDF/Word/TXT file paths or a directory
- `$KNOWLEDGE_BASE_NAME` — slug for this knowledge base (e.g., "hr-policy-2026")
- `$EMBED_MODEL` — embedding model (default: from args/llm_config.yaml)
- `$CHUNK_SIZE` — token chunk size for ingestion (default: 512)

## Steps

### 1. Ingest Documents
```bash
python tools/rag/ingest.py \
  --sources $DOCUMENT_SOURCES \
  --kb $KNOWLEDGE_BASE_NAME \
  --chunk-size $CHUNK_SIZE \
  --json
```
**Expected output:** `{"chunks_indexed": N, "kb": "$KNOWLEDGE_BASE_NAME"}`

### 2. Validate Index
```bash
python tools/rag/health_check.py --kb $KNOWLEDGE_BASE_NAME --json
```
**Expected output:** `{"status": "healthy", "chunks": N}`

### 3. Deploy Q&A Endpoint
```bash
python tools/rag/deploy_qa.py \
  --kb $KNOWLEDGE_BASE_NAME \
  --route /qa/$KNOWLEDGE_BASE_NAME \
  --json
```
**Expected output:** `{"route": "/qa/$KNOWLEDGE_BASE_NAME", "status": "live"}`

### 4. Smoke Test
```bash
python tools/testing/health_check.py \
  --url http://localhost:5000/qa/$KNOWLEDGE_BASE_NAME \
  --query "What is the leave policy?" \
  --json
```

## Outputs
- Live Q&A route at `/qa/$KNOWLEDGE_BASE_NAME`
- Indexed knowledge base in `data/kb/$KNOWLEDGE_BASE_NAME/`
- Audit trail entry in `audit_trail` table

## Acceptance Criteria
- Returns relevant answer for a test query with confidence ≥ 0.7
- Response time < 5s on 1 GPU
- All ingested documents appear in chunk count

## Error Handling
- If `ingest.py` returns `chunks_indexed: 0`, check file encoding (must be UTF-8)
- If `deploy_qa.py` fails with `RouteConflict`, choose a different `$KNOWLEDGE_BASE_NAME`
