# Phase 2 — AI-ify Determination: aiify-rm-06d89-phase-6111

**Opportunity:** 6111 (scan_id 43, roadmap rm-06d89040cf)
**Pattern:** `hardcoded_threshold → anomaly_detection`
**External module:** `src/paperless/parsers/tesseract.py` (ephemeral clone `aiify_git_zwu66zfu`, external/unmodifiable)
**Model recommendation:** claude-haiku-4-5-20251001

## Determination: DUPLICATE — closed, no code authored

This opportunity is an exact sibling of **6104 / 6105 / 6106** — all `hardcoded_threshold → anomaly_detection`
on the SAME external paperless OCR file `src/paperless/parsers/tesseract.py`. The faithful internal analog
(OCR per-word confidence anomaly detection) was already built as **aiify-opp-6105** in the Document
Intelligence Canvas (DIC).

### Existing implementation (commit `a26da9644`, irad/feature — IS HEAD)
`tools/document_intelligence/extractors.py`:
- `_classify_ocr_confidence` (L1081) — confident/uncertain/garbled bands on 0–100
- `_compute_ocr_confidence_anomalies` (L1116) — stdev outlier pass over per-word confidences
- `_ai_ocr_anomaly_severity` (L1184) — best-effort LLM severity grade (router key `dic_ocr_confidence_anomaly_severity`), degrades to deterministic baseline
- `detect_ocr_confidence_anomalies` (L1255) — public detector
- `_extract_word_confidences` (L1298) — pytesseract `image_to_data` per-word confidence
- `ocr_image_with_quality` (L1344) — annotates OCR output with quality WITHOUT changing extracted text

### Verification
- HEAD `a26da9644` (irad/feature) — the 6105 impl commit itself.
- 36/36 `tests/test_dic_ocr_confidence_anomaly.py` pass.
- External clone `aiify_git_zwu66zfu` reaped by the AI-ify engine (external/unmodifiable regardless).

The AI-ify engine re-emits this paperless OCR opp every scan; authoring a competing detector would collide
with the existing DIC OCR-quality layer (6059 batch yield + 6118 LLM cleanup + 6105 per-word confidence).

**Disposition:** moved to done with `bypass_verification:true` + `bypass_reason` (no code → no verification row).
