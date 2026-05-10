# Configure GovCon Opportunity Scanner

Set up the SAM.gov scanner for your company's pursuit priorities.

## Configuration Fields

**Company Keywords** — Terms that describe your core capabilities. The scanner scores each opportunity synopsis against these keywords. Be specific:
- Good: `zero trust architecture`, `DevSecOps`, `IL5 cloud migration`, `STIG compliance`
- Too broad: `IT services`, `consulting`, `technology`

**NAICS Codes** — Your company's registered NAICS codes (comma-separated). The scanner uses these as the primary filter. Example: `541511, 541512, 541519, 541690`

**Target Agencies** — Filter to specific agencies (optional but recommended):
- Leave blank to scan all federal agencies
- Select specific agencies to focus on your market segment

**Contract Value Range** — Minimum and maximum contract value to consider. Filters out opportunities below your threshold and above your bonding capacity.

**Scan Frequency** — How often the scanner checks SAM.gov:
- **Daily** (recommended): Morning scan, results in your inbox by 7 AM
- **Twice daily**: Morning + afternoon (catches amendments)
- **On-demand**: Manual trigger only

## Alert Settings

**High-fit threshold** — Score cutoff for "high-fit" designation. Default: 0.75 (75% keyword match). Lower to catch more opportunities; raise to tighten focus.

**Email alerts** — Receives immediate alerts when high-fit opportunities are found, even outside the daily scan window.
