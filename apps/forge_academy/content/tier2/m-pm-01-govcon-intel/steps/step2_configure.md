---
ontology_id: icdev:mission:m-pm-01-govcon-intel:step:2
step_class: icdev:Lesson
---

# Configure GovCon Opportunity Scanner

Scanner settings live in `args/govcon_config.yaml`, not in a form: change the file and the next scan picks them up. This step walks through what each setting does and what a well-tuned one looks like for your firm.

## What is configurable today (`args/govcon_config.yaml`)

| Setting | Key | Default |
|---|---|---|
| NAICS codes to scan | `sam_gov.naics_codes` | 8 IT and consulting codes (541511, 541512, 541519, 518210, …) |
| Notice types | `sam_gov.notice_types` | `o` solicitation, `p` pre-solicitation, `r` sources sought / RFI, `k` combined |
| How far back each scan looks | `sam_gov.lookback_days` | 30 |
| Scan cadence (daemon mode) | `scheduling.opportunity_scan_interval_hours` | 6, with quiet hours 02:00-06:00 UTC |
| Capability keywords per domain | `requirement_extraction` / `capability_mapping` blocks | DevSecOps, AI/ML, ATO/RMF, cloud, cyber, … |

The API key is read from the `SAM_GOV_API_KEY` environment variable. A one-off scan can also be narrowed on the command line: `python tools/govcon/sam_scanner.py --scan --naics 541512 --notice-type r --json`.

## Settings worth deciding for your firm

**Capability keywords** — Terms that describe your core capabilities. Extracted requirements are matched against them. Be specific:

- Good: `zero trust architecture`, `DevSecOps`, `IL5 cloud migration`, `STIG compliance`
- Too broad: `IT services`, `consulting`, `technology`

**NAICS codes** — Your company's registered NAICS codes. The scanner queries SAM.gov per NAICS code, so this list is the primary filter. Example: `541511, 541512, 541519, 541690`

**Target agencies and contract value** — Not scanner settings today. Apply them when you review results (Step 3): an opportunity outside your agencies or your value range is a no-bid, however well it matches.

**Scan frequency** — Every 6 hours by default in daemon mode. Amendments arrive throughout the day, so a shorter interval matters most late in a pursuit; SAM.gov's daily request limit (`sam_gov.rate_limit.daily_limit`) is the ceiling.

## Your task

Write down the NAICS codes, notice types, lookback and capability keywords you would set for your firm, and one keyword you would *remove* from the defaults because it generates noise for you.
