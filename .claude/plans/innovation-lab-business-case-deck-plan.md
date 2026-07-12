# Plan: Innovation Lab Executive Investment Deck

## Objective
Create a 10–12 slide internal executive deck inside ICDEV's Slides canvas that makes the business case for investing in an Innovation Lab. The deck must answer the executive questions **"So What?"**, **"Why Now?"**, and **"Why us / why not another lab?"** while weaving in capabilities from `ICDEV_Capabilities_Challenges_Solutions.xlsx` and providing editable ROI placeholder models.

## Scope Boundaries
- **Audience:** Internal company executives only (no customer/partner classification gates needed).
- **Format:** Curated deck script under `tools/slides/curated_decks/` using the existing `tools/slides/pptx_builder.py` engine.
- **Visual style:** Narrative / strategic — no product screenshots or live demo embeds.
- **Length:** 10–12 slides.
- **Excel integration:** Parse `ICDEV_Capabilities_Challenges_Solutions.xlsx`, extract selected challenges/solutions/capabilities, and use them to substantiate the differentiation and ROI story.
- **ROI:** Build editable placeholder formulas for customer opportunity pipeline, recruitment pipeline, and partner-hosting revenue/cost-avoidance.

## Proposed Slide Arc (12 slides)
1. **Title** — {company} Innovation Lab: Not Another Lab (company resolved from the own_company GovCon profile)
2. **The Ask** — Investment thesis: Infrastructure → Apps/AI/Agents → Hosting
3. **Why Now?** — Market window (AI/ML, Agentic AI, Digital Twin, DoD/IC interoperability demand)
4. **So What?** — The four executive outcomes: pipeline, recruitment, thought leadership, customer stickiness
5. **Our Differentiators** — What separates our lab from every other innovation lab (extracted from Excel differentiators)
6. **Capability Stack** — Infrastructure as the foundation; Application/AI/Agentic development on top; Hosting as the outward-facing showcase
7. **Use Case 1: AI/ML & Agentic AI Factory** — From concept to deployed, governed agents
8. **Use Case 2: Digital Twin & Interoperability Testbed** — Mirror customer environments for POCs
9. **Use Case 3: Partner/Vendor Showcase Hosting** — Co-demo SOTA products with customers
10. **ROI Model** — Editable placeholders: RFI/RFP pipeline, re-shaped opportunities, recruitment lift, partner sponsorship/hosting value
11. **Risk Mitigation & Path Forward** — Phased build, fast wins, governance
12. **Outro / Ask** — Decision points and next steps

## Implementation Steps
1. **Read existing patterns.** Confirm `tools/slides/pptx_builder.py`, `tools/slides/db/init_db.py`, and `tools/slides/curated_decks/icdev_executive_overview.py` behavior.
2. **Parse the Excel file.** Add a small parser inside the new curated script to read the relevant rows from the `Challenges & Solutions` and `Capability Catalog` sheets (or inline the already-extracted highlights to avoid a hard dependency on `openpyxl`).
3. **Create `tools/slides/curated_decks/innovation_lab_business_case.py`.**
   - Define `DECK_TITLE`, `THEME`, `DECK_TYPE`, and a 12-slide `SLIDES` list.
   - Embed Excel-derived differentiators as bullets/speaker notes.
   - Add editable ROI placeholder text and formulas in speaker notes (e.g., "Assumptions: $X active opportunities, Y% win-rate lift, Z partner sponsors @ $W/year").
   - Insert a `slides_decks` record and `slides_slides` rows via `get_connection()`.
   - Call `pptx_builder.build(...)` and update the deck record.
4. **Run the script.** Verify deck_id and PPTX path are emitted without errors.
5. **Spot-check the PPTX.** Open via Playwright or local inspection to confirm 12 slides, midnight_executive theme, and correct content flow.
6. **Register/update manifest/docs only if required.** Since this is a curated deck script (not a new canvas/tool), the only required registrations are:
   - Mention in `tools/slides/README.md` or `tools/manifest/slides.md` if such a shard exists.
   - Companion sync and coherence check per standard checklist.
7. **Commit.** Follow existing branch conventions; no destructive operations.

## Validation Criteria
- Script runs successfully and returns a valid `deck_id` and `.pptx` path.
- Generated deck contains exactly 12 slides.
- Slide titles match the arc above.
- At least 3 bullets per content slide reference Excel-derived capabilities/challenges.
- ROI placeholders appear on slide 10 and are clearly editable (e.g., bracketed assumptions / speaker-note formulas).
- No screenshots or demo embeds.

## Dependencies & Risks
- **Dependency:** `python-pptx` and the existing slides DB must be healthy. Pre-flight: run a quick `python -c "from tools.slides.db.init_db import init_db; init_db()"`.
- **Risk:** Curated script hard-codes Excel data, which can drift. Mitigation: keep parser minimal or document the Excel sheet/row references in comments.
- **Risk:** LLM/content agents are not needed for this task; we bypass them by using the curated-deck pattern, which is deterministic and fast.

## Files to Create/Modify
- **Create:** `tools/slides/curated_decks/innovation_lab_business_case.py`
- **Possibly update:** `tools/manifest/slides.md` (if it exists) with a one-line entry for the new curated deck.
- **Run:** `python tools/slides/curated_decks/innovation_lab_business_case.py`

## Success Statement
A 12-slide, internally-facing, narrative/strategic PowerPoint deck is generated from a deterministic curated script, is persisted in the ICDEV slides DB, and can be downloaded/iterated by the user.
