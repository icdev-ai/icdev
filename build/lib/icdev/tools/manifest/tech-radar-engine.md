# Tech Radar Engine (D352 — pint-techrad)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Tech Radar Engine (D352 — pint-techrad)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Radar Engine | tools/tech_radar/radar_engine.py | Main assessment engine for the ICDEV™ Tech Radar. Run cycle: fetch external signals via source_scanner → recompute composite_score per entry (weights: ecosystem_maturity 0.35, icdev_fit 0.30, airgap_compat 0.25, il_compliance 0.10) → assign ring by threshold → UPDATE tech_radar_entries if ring changes → INSERT append-only row to tech_radar_history → cross-register ring→adopt promotions to innovation_signals. Supports daemon mode with configurable interval. | --run, --status, --list [--ring adopt\|trial\|assess\|hold], --daemon [--interval SECS], --json | Assessment results / ring summary / entry list (JSON or text) |
| Source Scanner | tools/tech_radar/source_scanner.py | Technology signal source scanner (D352 registry pattern). SOURCE_SCANNERS dict maps source name → scan function. Three sources: thoughtworks_radar (Thoughtworks Tech Radar JSON, vol 31 2024), cncf_landscape (CNCF landscape.yml GRADUATED/INCUBATING/SANDBOX maturity signals; requires PyYAML), github_trending (GitHub API recently-starred repos, star-velocity buckets: VIRAL/HIGH_ADOPTION/GROWING/EMERGING). Each function returns normalized signal dicts: {name, source, ecosystem_maturity_signal, description}. All HTTP calls wrapped with 10s timeout — air-gap safe, failures return [] with warning, no external runtime deps beyond stdlib (PyYAML optional). | --scan --all, --scan --source NAME (thoughtworks_radar \| cncf_landscape \| github_trending), --json | List of normalized signal dicts |

### DB Tables
| Table | Owner | Notes |
|-------|-------|-------|
| `tech_radar_entries` | radar_engine.py | Updatable — ring changes on each assessment cycle; migration 070; fields: id, name, category, current_ring, previous_ring, ecosystem_maturity, icdev_fit, airgap_compat, il_compliance, composite_score, rationale, last_assessed, classification. Ring CHECK constraint: adopt \| trial \| assess \| hold. Index: idx_techrad_ring(current_ring). |
| `tech_radar_history` | radar_engine.py | Append-only per NIST AU — records every ring transition; migration 070; fields: id, entry_id, from_ring, to_ring, composite_score, innovation_signal_id, changed_at. Index: idx_techrad_history_entry(entry_id). |
| `innovation_signals` | radar_engine.py | Cross-registration target; ring→adopt transitions written here with source='tech_radar', signal_type='technology_promotion'. |

### Ring Definitions
| Ring | Composite Score Threshold | Meaning |
|------|--------------------------|---------|
| adopt | ≥ 0.75 | Recommended for immediate use in ICDEV™ projects |
| trial | ≥ 0.60 | Worth pursuing in low-risk projects; evaluate fit |
| assess | ≥ 0.45 | Worth researching; not ready for production use |
| hold | ≥ 0.00 | Proceed with caution; avoid new adoption |

### Scoring Weights
| Dimension | Weight | Description |
|-----------|--------|-------------|
| ecosystem_maturity | 0.35 | External signal from Thoughtworks/CNCF/GitHub; boosted by ADOPT/GRADUATED/VIRAL signals |
| icdev_fit | 0.30 | Keyword overlap with ICDEV™ toolchain (playwright, terraform, ruff, trivy, etc.) |
| airgap_compat | 0.25 | Air-gap suitability; scores < 0.70 apply −0.05 composite penalty |
| il_compliance | 0.10 | IL4/IL5/IL6 compliance evidence |

### Seeded Technologies (Migration 070)
| Name | Category | Seed Ring | Composite |
|------|----------|-----------|-----------|
| rspack | build_tooling | trial | 0.67 |
| ast-grep | sast_code_intel | trial | 0.65 |
| Hypothesis | testing | adopt | 0.84 |
| uv | package_management | adopt | 0.79 |
| ruff | linting_formatting | adopt | 0.83 |
| Playwright | e2e_testing | adopt | 0.78 |
| Trivy | vulnerability_scanning | adopt | 0.84 |
| OpenTelemetry | observability | adopt | 0.84 |
| Terraform | iac | adopt | 0.87 |
| Ansible | configuration_management | adopt | 0.88 |

### Air-Gap Behavior
- `source_scanner.py`: all HTTP calls wrapped with 10s timeout; returns `[]` on any network error — safe in air-gapped environments. PyYAML optional (only needed for cncf_landscape source).
- `radar_engine.py`: degrades gracefully when scanner returns empty signals (no crash, skips signal-boost step). Daemon mode continues cycling.
