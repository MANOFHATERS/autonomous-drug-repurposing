-- Rollback for migration 012: revert confidence_tier CHECK to old vocabulary.
-- WARNING: this rollback re-introduces BUG #4 (Piñero tier mislabel).
-- Use only if you need to revert to a pre-V100 state.

ALTER TABLE gene_disease_associations DROP CONSTRAINT IF EXISTS chk_gda_confidence_tier;
ALTER TABLE gene_disease_associations ADD CONSTRAINT chk_gda_confidence_tier
    CHECK (confidence_tier IS NULL OR confidence_tier IN
           ('weak', 'moderate', 'strong'));

-- Migrate 'sub_weak' and 'weak' rows back to the old labels.
-- 'sub_weak' → 'weak' (old code labeled [0.0, 0.06) as 'weak')
-- 'weak'     → 'moderate' (old code labeled [0.06, 0.3) as 'moderate')
UPDATE gene_disease_associations SET confidence_tier = 'weak'     WHERE confidence_tier = 'sub_weak';
UPDATE gene_disease_associations SET confidence_tier = 'moderate' WHERE confidence_tier = 'weak';
