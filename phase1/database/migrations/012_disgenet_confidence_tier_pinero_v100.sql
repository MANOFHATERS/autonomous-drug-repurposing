-- Migration 012: DisGeNET confidence_tier CHECK constraint — Piñero 2020 alignment
-- V100 ROOT FIX (BUG #4, P0 CRITICAL)
--
-- The previous constraint (migration 004) accepted only
--   ('weak', 'moderate', 'strong')
-- which inverted Piñero et al. 2020 §2.3 vocabulary: the [0.06, 0.3)
-- band was mislabeled "moderate" when it is actually the WEAK-evidence
-- band. This caused every weak-evidence GDA edge to be mislabeled
-- "moderate", inflating perceived confidence and training models on
-- weak evidence as if it were moderate.
--
-- This migration ALTERs the constraint to accept Piñero's actual
-- vocabulary: ('sub_weak', 'weak', 'strong'). Existing rows with
-- confidence_tier='moderate' are migrated to 'weak' (the correct
-- Piñero label for the [0.06, 0.3) band) before the constraint is
-- replaced, so no data is lost.

-- Step 1: migrate existing 'moderate' rows to 'weak' (correct Piñero label).
UPDATE gene_disease_associations
SET confidence_tier = 'weak'
WHERE confidence_tier = 'moderate';

-- Step 2: replace the CHECK constraint.
ALTER TABLE gene_disease_associations DROP CONSTRAINT IF EXISTS chk_gda_confidence_tier;
ALTER TABLE gene_disease_associations ADD CONSTRAINT chk_gda_confidence_tier
    CHECK (confidence_tier IS NULL OR confidence_tier IN
           ('sub_weak', 'weak', 'strong'));
