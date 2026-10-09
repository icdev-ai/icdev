# CUI // SP-CTI
"""BOM Evidence Engine.

Turns a pile of disparate documents — spreadsheets, decks, PDFs, diagrams — into
one reconciled bill of materials that a person can defend in front of the people
who control the money.

Three things make that possible, and they are all deliberate:

1. **The grid is preserved.** We read workbooks cell by cell, keeping formulas
   and coordinates. Text extraction throws both away, and with them goes any
   hope of telling a double-count from a genuine second unit, or of citing the
   exact cell a number came from.

2. **A model never touches a number.** LLMs adjudicate identity ("are these two
   rows the same product?") and propose category names. They cannot emit a
   figure, invent a string that is not in the source, or approve anything. Those
   are code-level constraints, not politely-worded prompts.

3. **Silence is never confirmation.** An option nobody has chosen is worth zero,
   not the cheapest branch. A repurposed asset with no inventory to verify it is
   flagged, not assumed. An unknown price basis raises a finding instead of
   defaulting to something convenient.

Nothing here knows what an innovation lab is. It knows how to ask whether a BOM
funds the design that was agreed, spends only what that design justifies, and
adds up.
"""
