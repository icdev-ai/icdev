# Voice-of-Customer (VOC)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## VOC Signal Capture

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Transcript Ingestor | tools\voc\transcript_ingestor.py | Ingests .txt/.md/.pdf/.docx transcripts, extracts job-statement sentences via keyword matching, persists to voc_documents + voc_job_statements via get_connection() | --file PATH, --upload-dir DIR, --json | JSON {documents, job_statements} |
| VOC Engine | tools\voc\voc_engine.py | Orchestrates full VOC pipeline: scans upload_dir for unprocessed transcripts, ingests via TranscriptIngestor, clusters statements by keyword-overlap, scores clusters, emits creative_feature_gaps signals for high-scoring clusters | --run, --json | JSON {documents_processed, job_statements, signals_created} |
