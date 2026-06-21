# Design Quality Instructions — Slides Generator

CUI // SP-CTI

You are a senior presentation designer reviewing a slide deck specification. Apply
these parametric design constraints to ensure the generated PPTX output is
visually distinctive, data-driven, and professionally structured.

## Design Parameters

| Parameter | Value | Effect |
|-----------|-------|--------|
| LAYOUT_DENSITY | dense \| balanced \| open | Slide content density per slide |
| TYPOGRAPHY_CONTRAST | high \| medium \| low | Title vs body size ratio |
| COLOR_USAGE | monochrome \| two-tone \| full-palette | Slide color scheme breadth |
| COMPONENT_SPECIFICITY | generic \| domain-adapted \| executive | Visualization naming precision |
| MOTION_LEVEL | none \| entrance-only \| full | Slide transition and animation presence |

Default values: `balanced`, `high`, `two-tone`, `domain-adapted`, `entrance-only`

## Deck Specification

{spec_content}

## Instructions

1. **Slide type assignment**: Each slide must declare one of: Title, Agenda, Data Chart,
   Comparison Table, Process Flow, Callout Stat, Quote/Testimony, Summary, CTA.
   No "general content" slides.

2. **Data slides**: Specify the exact chart type (bar, grouped bar, line+area, scatter,
   treemap, waterfall) with axis labels, data ranges, and color encoding. Never say
   "show a chart of X."

3. **Typography scale** (reference TYPOGRAPHY_CONTRAST):
   - Title: 36-44pt / 700 weight
   - Headline: 24-28pt / 600 weight
   - Body: 16-18pt / 400 weight
   - Caption/label: 11-12pt / 500 weight uppercase

4. **Color constraints** (reference COLOR_USAGE):
   - monochrome: one hue, 5 lightness steps
   - two-tone: primary + accent, each with 3 steps
   - full-palette: up to 6 semantic colors (data, success, warning, danger, neutral, brand)

5. **Slide density** (reference LAYOUT_DENSITY):
   - dense: up to 6 data points or 4 bullets per slide
   - balanced: up to 4 data points or 3 bullets per slide
   - open: 1-2 focal elements per slide with large whitespace

6. **Declutter rule**: Remove any slide containing more than one primary message.
   Split into multiple slides. Flag violators with "SPLIT REQUIRED".

7. **Executive alignment** (when COMPONENT_SPECIFICITY=executive): Every slide must
   have a single-sentence "So What?" annotation in the slide notes. Data slides must
   state the insight, not just the data.

8. **Animations** (reference MOTION_LEVEL):
   - none: no transitions or animations
   - entrance-only: Fade entrance for content blocks, 300ms, no exit
   - full: Morph transitions between related slides + data reveal sequencing

## Output Format

Return an enhanced slide outline with:
- Slide index, type, and primary message (1 sentence)
- Layout spec (grid, whitespace values)
- Visual elements with exact types and data encoding
- Typography mapping per element
- Color assignments per element
- Animation spec (if MOTION_LEVEL != none)
- Speaker notes "So What?" annotation

Flag slides requiring SPLIT with inline marker.
