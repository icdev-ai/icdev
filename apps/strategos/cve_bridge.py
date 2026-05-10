# =============================================================================
# Strategos Cyber Dashboard — Schema Reference (sgx-cve-01 findings)
# =============================================================================
#
# CYBER PAGE ROUTES (apps/strategos/blueprint.py)
# -----------------------------------------------
# GET /strategos/cyber                       → renders strategos/cyber.html
# GET /strategos/api/cyber/infra-nodes       → infra map nodes (supply + cyber ops)
# GET /strategos/api/cyber/attack-heatmap    → MITRE ATT&CK technique heatmap
# GET /strategos/api/cyber/timeline          → dual-axis timeline (cyber + kinetic)
#   All accept optional ?aoi=<ukraine|taiwan> filter.
#
# PRIMARY TABLE: sg_conflict_events
# ----------------------------------
# Used for all cyber API endpoints (event_type = 'cyber_op').
# Columns relevant to CVE / threat display:
#   id               TEXT        — unique event ID (used as correlationid)
#   event_type       TEXT        — 'cyber_op' for cyber events
#   severity         TEXT        — severity label (e.g. 'critical','high','medium','low')
#   description      TEXT        — event/CVE title or description
#   event_ts         TIMESTAMPTZ — when the event/CVE was detected
#   technique_ids    TEXT        — comma-separated MITRE ATT&CK IDs (e.g. 'T1190,T1566')
#   threat_actor     TEXT        — APT group / actor name (maps to source signal ID)
#   malware_family   TEXT        — associated malware family
#   confidence       INTEGER     — confidence score 0-100
#   attack_vector    TEXT        — initial access vector
#   target_name      TEXT        — targeted infrastructure/system label
#   target_type      TEXT        — infra type (power/rail/water/comms/generic)
#   lat              REAL        — geo-latitude of targeted node
#   lon              REAL        — geo-longitude of targeted node
#   aoi              TEXT        — area of interest (ukraine/taiwan)
#   actor1           TEXT        — primary actor
#   actor2           TEXT        — secondary actor
#   source           TEXT        — signal origin / feed name
#   external_id      TEXT        — source signal ID (maps from external CTI feed)
#   source_url       TEXT        — original report URL
#   goldstein_scale  REAL        — event intensity (-10 to +10, kinetic scale)
#   avg_tone         REAL        — media tone score
#   num_mentions     INTEGER     — media mention count
#   metadata_json    TEXT        — additional JSON payload
#   metadata         JSONB       — structured metadata
#   created_at       TIMESTAMPTZ — DB insertion time
#   updated_at       TEXT        — last update
#
# SECONDARY TABLE: sg_supply_nodes
# ---------------------------------
# Used for infrastructure map overlay (non-cyber-op nodes).
# Columns:
#   node_id          TEXT  — unique node ID
#   label            TEXT  — display label
#   node_type        TEXT  — infra type (power/rail/water/comms/generic)
#   criticality      TEXT  — criticality level (high/medium/low)
#   lat              REAL  — latitude
#   lon              REAL  — longitude
#   substitutability REAL  — supply substitutability score 0-1
#   controlled_by    TEXT  — controlling entity
#   metadata_json    TEXT  — additional JSON
#
# NOTE: There is NO dedicated sg_cyber_events table. All cyber operations
# are stored in sg_conflict_events with event_type='cyber_op'.
# There is NO cyber_vuln_score column in the DB — the frontend JS computes
# a synthetic score from confidence (0-100) scaled to 0-10 at render time.
#
# CVE DISPLAY COLUMN MAPPING (for cve_bridge import):
#   CVE Severity     → sg_conflict_events.severity
#   CVSS Score       → NOT stored natively; use confidence as proxy (0-10 scale)
#   Title/Summary    → sg_conflict_events.description
#   Source Signal ID → sg_conflict_events.external_id  (or threat_actor for actor ID)
# =============================================================================
