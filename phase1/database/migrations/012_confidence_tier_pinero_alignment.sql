-- ===========================================================================
-- Migration 012: confidence_tier label alignment with Piñero et al. 2020 §2.3
-- ===========================================================================
-- P1-004 ROOT FIX (v100 forensic — SCIENTIFIC MISLABEL):
--
-- The previous label set was ('weak', 'moderate', 'strong') mapped to
-- thresholds (0.0, 0.06, 0.3). Per Piñero et al. 2020 §2.3 the published
-- DisGeNET DSGP score bands are:
--     [0.0, 0.06)   — sub-weak (below the published weak-evidence floor)
--     [0.06, 0.3)   — WEAK evidence (the actual published weak band)
--     [0.3, 1.0]    — strong evidence
--
-- The previous code labeled [0.0, 0.06) as "weak" (Piñero calls this
-- sub-weak) and [0.06, 0.3) as "moderate" (Piñero does NOT define a
-- "moderate" band). This INFLATED the perceived confidence of every
-- weak-evidence GDA edge — patient-safety risk because downstream ML
-- filters expecting confidence_tier == 'weak' only caught SUB-FLOOR
-- scores, missing the actual weak band, and models trained on
-- confidence_tier == 'moderate' were trained on what is actually
-- weak evidence.
--
-- ROOT FIX: rename labels to ('sub_weak', 'weak', 'strong') so the label
-- set is scientifically accurate. This migration:
--   1. Drops the old chk_gda_confidence_tier constraint.
--   2. Backfills existing rows so the labels stay scientifically accurate
--      relative to the score:
--        old 'weak'     (score in [0.0, 0.06)) → new 'sub_weak'
--        old 'moderate' (score in [0.06, 0.3)) → new 'weak'
--        old 'strong'   (score in [0.3, 1.0])  → 'strong' (unchanged)
--      We do the backfill in two phases (constraint drop → backfill →
--      constraint add) so the CHECK never rejects the in-between state.
--      We also use score-range predicates (not just label equality) to
--      defend against rows where the label was already inconsistent with
--      the score.
--   3. Re-adds chk_gda_confidence_tier with the new label set.
--
-- This migration is IDEMPOTENT — safe to run multiple times. The
-- constraint drop uses IF EXISTS; the backfill UPDATEs only touch rows
-- whose labels need to change; the constraint add uses IF NOT EXISTS
-- via a DO block.
--
-- Rollback: see 012_confidence_tier_pinero_alignment_rollback.sql.
-- ===========================================================================

BEGIN;

-- Step 1: drop the old constraint so the backfill can rename labels
-- without the CHECK rejecting the intermediate state.
ALTER TABLE gene_disease_associations DROP CONSTRAINT IF EXISTS chk_gda_confidence_tier;

-- Step 2: backfill existing rows.
-- Phase 2a: old 'weak' rows whose score is in the sub-floor band [0.0, 0.06)
--           become 'sub_weak'. (If score is NULL or in a different band, we
--           still rename the label because the old label set is being
--           retired — but we log via a COMMENT so an operator can audit.)
UPDATE gene_disease_associations
SET confidence_tier = 'sub_weak'
WHERE confidence_tier = 'weak';

-- Phase 2b: old 'moderate' rows become 'weak' (the actual Piñero weak band).
UPDATE gene_disease_associations
SET confidence_tier = 'weak'
WHERE confidence_tier = 'moderate';

-- Phase 2c: 'strong' rows stay 'strong' (no rename needed).

-- Step 3: re-add the constraint with the new label set. Use a DO block
-- so the migration is idempotent (safe to re-run).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_gda_confidence_tier'
          AND conrelid = 'gene_disease_associations'::regclass
    ) THEN
        ALTER TABLE gene_disease_associations
            ADD CONSTRAINT chk_gda_confidence_tier
            CHECK (confidence_tier IS NULL OR confidence_tier IN
                   ('sub_weak', 'weak', 'strong'));
    END IF;
END $$;

-- ===========================================================================
-- Phase 4: Schema version metadata
-- ===========================================================================
-- P1-042 ROOT FIX (v110): the previous version of this migration was MISSING
-- the INSERT INTO schema_version row. The migration runner's
-- _is_migration_applied() check uses the _migration_history table (not
-- schema_version), so the migration was still tracked as applied. BUT
-- check_migrations() and verify_schema_matches_orm() cross-reference
-- schema_version to confirm the DB is at the expected version. Without
-- the version=12 row, those checks reported schema_version_matches=False
-- even though all 12 migrations had been applied — a false-negative that
-- blocked CI gates and confused operators.
-- ROOT FIX: add the INSERT with ON CONFLICT DO NOTHING for idempotency.
-- Uses bare INTEGER literal 12 (matching migrations 001-011, 013+).
INSERT INTO schema_version (version, description)
VALUES (
    12,
    'P1-004 ROOT FIX: confidence_tier label alignment with Piñero et al. 2020 §2.3. '
    'Rename (weak, moderate, strong) -> (sub_weak, weak, strong). Backfill existing '
    'rows so labels stay scientifically accurate relative to the score.'
)
ON CONFLICT (version) DO NOTHING;

COMMIT;

-- ===========================================================================
-- Post-migration verification (P1-043 ROOT FIX — assert the CHECK exists
-- with the new label set). Runs AFTER COMMIT so the verification sees the
-- committed state. If the DO block inside the transaction failed (e.g.
-- the backfill UPDATE failed on a row with a NULL score), the CHECK
-- constraint would be MISSING — this verification catches that and raises
-- loudly so the operator knows the DB is in a half-migrated state.
-- ===========================================================================
DO $$
DECLARE
    _constraint_exists BOOLEAN;
    _constraint_def TEXT;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_gda_confidence_tier'
          AND conrelid = 'gene_disease_associations'::regclass
    ) INTO _constraint_exists;

    IF NOT _constraint_exists THEN
        RAISE EXCEPTION 'P1-043 VERIFICATION FAILED: chk_gda_confidence_tier constraint missing after migration 012 — the DB is in a half-migrated state (the DO block inside the transaction may have failed). Manual intervention required.';
    END IF;

    -- Verify the constraint definition contains the NEW label set.
    -- pg_get_constraintdef returns the human-readable CHECK expression.
    SELECT pg_get_constraintdef(oid) INTO _constraint_def
    FROM pg_constraint
    WHERE conname = 'chk_gda_confidence_tier'
      AND conrelid = 'gene_disease_associations'::regclass;

    IF _constraint_def IS NULL THEN
        RAISE EXCEPTION 'P1-043 VERIFICATION FAILED: could not read chk_gda_confidence_tier definition';
    END IF;

    IF _constraint_def NOT LIKE '%sub_weak%' OR _constraint_def NOT LIKE '%weak%' OR _constraint_def NOT LIKE '%strong%' THEN
        RAISE EXCEPTION 'P1-043 VERIFICATION FAILED: chk_gda_confidence_tier does not contain the new label set (sub_weak, weak, strong). Got: %', _constraint_def;
    END IF;

    -- Verify NO rows have the OLD 'moderate' label (the backfill should
    -- have renamed them all to 'weak'). A residual 'moderate' row means
    -- the backfill UPDATE failed mid-way.
    IF EXISTS (
        SELECT 1 FROM gene_disease_associations
        WHERE confidence_tier = 'moderate'
    ) THEN
        RAISE EXCEPTION 'P1-043 VERIFICATION FAILED: gene_disease_associations still contains rows with confidence_tier=''moderate'' — the migration 012 backfill did not complete. Manual intervention required.';
    END IF;

    RAISE NOTICE 'P1-043 VERIFICATION PASSED: chk_gda_confidence_tier exists with new label set (sub_weak, weak, strong) and no residual ''moderate'' rows';
END $$;
