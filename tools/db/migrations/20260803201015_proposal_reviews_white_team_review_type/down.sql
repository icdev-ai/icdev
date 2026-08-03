-- CUI // SP-CTI
-- Rollback: narrow proposal_reviews.review_type back to the pre-white_team set.
--
-- This will FAIL if any row already holds review_type='white_team' — PostgreSQL
-- validates a new CHECK against existing rows. That is the correct behaviour:
-- silently deleting or rewriting a recorded review to make a rollback succeed
-- would destroy evidence of a review that actually happened. Reassign or remove
-- those rows deliberately first.

-- @pg-only
ALTER TABLE proposal_reviews DROP CONSTRAINT IF EXISTS proposal_reviews_review_type_check;

ALTER TABLE proposal_reviews ADD CONSTRAINT proposal_reviews_review_type_check
    CHECK (review_type IN (
        'pink_team', 'red_team', 'gold_team', 'white_glove', 'internal'));

-- @sqlite-only
-- No-op, mirroring up.sql.
SELECT 1;
