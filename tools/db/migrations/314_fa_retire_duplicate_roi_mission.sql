-- CUI // SP-CTI
-- Migration 314: retire the duplicate "AI ROI Framework" mission (aca-hon-03).
--
-- Two active missions carried the title "AI ROI Framework":
--
--   m-leader-02-roi         tier 2, ZERO steps, hand-written catalogue entry
--   m-leadership-01-ai-roi  tier 2, 3 steps, authored content
--
-- The m-leader-* family is an older seed generation superseded by m-leadership-*.
-- The duplicate has no steps at all, so it is a "Coming soon" dead end that no
-- learner can complete, sitting next to the real mission with an identical title.
--
-- retire_superseded_missions() deliberately will not touch this one: it only retires
-- rows marked as DERIVED, and this is a hand-written catalogue entry. Removing a
-- curated mission is a content decision, so it is recorded here explicitly rather
-- than inferred at start-up.
--
-- is_active=0 rather than DELETE: any learner progress rows and the audit trail must
-- survive. The BUILTIN_MISSIONS upsert only refreshes title/tagline/xp_reward/
-- order_idx on conflict, never is_active, so this retirement is not undone by the
-- next seed.
--
-- m-leader-03-exec-dash declares m-leader-02-roi as a prerequisite. Left pointing at
-- a retired mission that prerequisite can never be satisfied, so it is repointed to
-- the surviving m-leader-01-ai-maturity in the same track.
--
-- Idempotent: re-running matches nothing.

UPDATE fa_missions
SET is_active = 0
WHERE slug = 'm-leader-02-roi'
  AND is_active = 1
  AND NOT EXISTS (
        SELECT 1 FROM fa_mission_steps s
        WHERE s.mission_id = fa_missions.id
  );

UPDATE fa_missions
SET prereq_slugs_json = '["m-leader-01-ai-maturity"]'
WHERE slug = 'm-leader-03-exec-dash'
  AND prereq_slugs_json LIKE '%m-leader-02-roi%';
