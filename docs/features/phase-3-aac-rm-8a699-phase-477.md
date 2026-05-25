# AAC Phase 3 Assessment — hardcoded_threshold in requests/utils.py

**Task ID:** aac-rm-8a699-phase-477  
**Roadmap:** rm-8a699d41b6 (AI Augmentation Roadmap — Scan 40)  
**Phase:** Phase 3 — Long-Horizon Investments  
**Pattern:** hardcoded_threshold  
**Source:** `psf/requests` (external dependency, scan_id=40)  
**File:** `src/requests/utils.py`, line 628  
**Scores:** composite=0.4387, value=0.5, feasibility=0.325, risk=0.5

## Finding

The AAC scanner flagged `len(h) == 2` in the `unquote_unreserved` function:

```python
def unquote_unreserved(uri):
    parts = uri.split("%")
    for i in range(1, len(parts)):
        h = parts[i][0:2]
        if len(h) == 2 and h.isalnum():  # ← flagged: hardcoded threshold
            ...
```

## Assessment

**False positive / protocol constant.** The literal `2` is a structural requirement of
RFC 3986 percent-encoding: every `%XX` sequence must be exactly 2 hex characters. This
cannot be made configurable without violating the URI specification.

ML anomaly detection (`ai_paradigm: anomaly_detection`) has no applicable role here —
the constant defines valid encoding structure, not a performance or behavioral threshold.

## Recommendation

- No code change warranted (external dependency; protocol constant by spec).
- AAC scanner should add a suppression rule for `len(h) == N` patterns inside RFC-defined
  parsing functions to reduce false-positive rate in future scans.
- Low feasibility score (0.325) is consistent with this assessment.

## Status

Closed — no action required. Documented for AAC scanner calibration.
