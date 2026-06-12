# CUI // SP-CTI
"""OAuth / OIDC tunable constants.

Hardcoded thresholds originally inline in oauth_auth.py have been extracted
here so operators can tune them without touching code.  Anomaly detection
constants govern statistical outlier detection on JWKS fetch latency.
"""

# ── Cache & fetch tunables ─────────────────────────────────────────────────
JWKS_CACHE_TTL_SECONDS = 3600
JWKS_FETCH_TIMEOUT_SECONDS = 10

# ── Anomaly detection (JWKS fetch latency) ───────────────────────────────────
# Lightweight statistical outlier detection.  A ring buffer of recent fetch
# latencies is maintained per JWKS endpoint.  A fetch is flagged anomalous if
# its latency exceeds the historical mean by k standard deviations, provided
# we have enough samples to form a distribution.  An absolute ceiling catches
# catastrophic latency even when the baseline is sparse.
JWKS_LATENCY_ANOMALY_STDEV_K = 2.0
JWKS_LATENCY_MIN_SAMPLES = 4
JWKS_LATENCY_ABS_CEILING_MS = 5000
JWKS_LATENCY_MAX_HISTORY = 20   # ring buffer size per endpoint
