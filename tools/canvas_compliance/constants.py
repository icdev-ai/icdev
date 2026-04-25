# CUI // SP-CTI
"""Thresholds for canvas compliance badge scoring (green / yellow / red)."""

# NDC — open POA&M count
NDC_POAM_YELLOW = 5   # >= this → yellow
NDC_POAM_RED = 10     # >= this → red

# SDC — threat model age (days since last assessment)
SDC_AGE_YELLOW = 30   # >= this → yellow
SDC_AGE_RED = 90      # >= this → red

# SDC — attack path count
SDC_PATHS_YELLOW = 1  # > 0 → yellow
SDC_PATHS_RED = 5     # >= this → red

# PDC — pipeline compliance score (%)
PDC_SCORE_GREEN = 90  # >= this → green
PDC_SCORE_YELLOW = 70 # >= this → yellow (else red)

# BDC — days-to-expiry for nearest ISA
BDC_EXPIRY_GREEN = 90   # > this → green
BDC_EXPIRY_YELLOW = 30  # > this → yellow (else red)

# DDC — PII exposure count
DDC_PII_GREEN = 0   # == 0 → green
DDC_PII_YELLOW = 5  # <= this → yellow (else red)

# ODC — MITRE coverage %
ODC_COV_GREEN = 80  # >= this → green
ODC_COV_YELLOW = 50 # >= this → yellow (else red)

# IDC — IaC drift count (drifted resources from latest drift check)
IDC_DRIFT_GREEN = 0  # == 0 → green
IDC_DRIFT_YELLOW = 3 # <= this → yellow (else red)
