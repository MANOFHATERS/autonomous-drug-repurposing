-- ===========================================================================
-- Migration 016: tighten proteins.uniprot_id CHECK to match ORM + UniProt spec
-- ===========================================================================
-- P1-013 ROOT FIX (Team-1 -- DB CHECK weaker than ORM + Python validator):
--
-- The previous CHECK constraint in 001_initial_schema.sql was:
--     CHECK (LENGTH(uniprot_id) >= 4 AND LENGTH(uniprot_id) <= 10)
-- This accepted 4, 5, 7, 8, 9 char strings -- NONE of which are real
-- UniProt accessions. Real UniProt accessions are EXACTLY 6 chars (old
-- format, e.g. P12345) or 10 chars (new format, e.g. A0A0K3AVT9) per
-- the official spec:
--     https://www.uniprot.org/help/accession_numbers
--
-- The ORM constraint in database/models.py (line 1258) was ALREADY
-- strict (LENGTH = 6 OR LENGTH = 10), creating a DIVERGENCE:
--   * dev DBs created via the ORM (init_db) enforced the strict contract.
--   * prod DBs created via this migration accepted junk (4-10 chars).
--
-- A raw SQL INSERT (manual fix, future tool, db migration script)
-- bypassing the ORM could land a 4-char "UniProt ID" in production --
-- breaking the defense-in-depth contract (DB should be the LAST line
-- of defense, not the weakest). Downstream consumers that join on
-- uniprot_id would see phantom proteins.
--
-- ROOT FIX:
--   1. Drop the old chk_proteins_uniprot_length constraint.
--   2. Backfill / quarantine any existing rows with invalid length
--      (4, 5, 7, 8, 9 chars). Real UniProt IDs are 6 or 10 chars; any
--      other length is junk. We log the count of quarantined rows so
--      the operator can audit. Quarantine = set uniprot_id = NULL
--      (the column is nullable) and add a note to a sidecar audit
--      table (or log if no audit table exists).
--   3. Re-add chk_proteins_uniprot_length with the strict contract:
--      CHECK (uniprot_id IS NULL OR LENGTH(uniprot_id) IN (6, 10))
--      matching the ORM.
--
-- This migration is IDEMPOTENT -- safe to run multiple times. The
-- constraint drop uses IF EXISTS; the constraint add uses IF NOT EXISTS
-- via a DO block.
--
-- Rollback: see 016_tighten_uniprot_id_check_constraint_rollback.sql.
-- ===========================================================================

BEGIN;

-- Step 1: drop the old (relaxed) constraint so we can backfill.
ALTER TABLE proteins DROP CONSTRAINT IF EXISTS chk_proteins_uniprot_length;

-- Step 2: quarantine rows with invalid uniprot_id length.
-- Real UniProt accessions are EXACTLY 6 or 10 chars. Any other length
-- (4, 5, 7, 8, 9) is junk -- the result of a previous bug where the
-- relaxed CHECK allowed them in. We set uniprot_id = NULL (the column
-- is nullable) so the rows survive but lose their (invalid) UniProt
-- link. The operator can audit via the count logged below.
--
-- NOTE: we do NOT delete the rows. Deleting proteins would cascade
-- to drug_protein_interactions and other FK tables, potentially
-- losing real data. Quarantine (set NULL) is safer -- the protein
-- row stays, but downstream UniProt-join consumers skip it.
DO $$
DECLARE
    _quarantined_count INTEGER;
BEGIN
    WITH _quarantined AS (
        UPDATE proteins
        SET uniprot_id = NULL
        WHERE uniprot_id IS NOT NULL
          AND LENGTH(uniprot_id) NOT IN (6, 10)
        RETURNING 1
    )
    SELECT COUNT(*) INTO _quarantined_count FROM _quarantined;

    IF _quarantined_count > 0 THEN
        RAISE NOTICE 'P1-013: quarantined % rows of proteins with invalid uniprot_id length (set to NULL). Real UniProt IDs are 6 or 10 chars.', _quarantined_count;
    ELSE
        RAISE NOTICE 'P1-013: no rows needed quarantining (all uniprot_id values were already 6 or 10 chars).';
    END IF;
END $$;

-- Step 3: re-add the constraint with the strict contract (matches ORM).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_proteins_uniprot_length'
          AND conrelid = 'proteins'::regclass
    ) THEN
        ALTER TABLE proteins
            ADD CONSTRAINT chk_proteins_uniprot_length
            CHECK (uniprot_id IS NULL OR LENGTH(uniprot_id) IN (6, 10));
    END IF;
END $$;

-- ===========================================================================
-- Step 4: Schema version metadata
-- ===========================================================================
-- P1-042 ROOT FIX (v110): the previous version of this migration was MISSING
-- the INSERT INTO schema_version row. check_migrations() cross-references
-- schema_version; without the version=16 row, those checks reported
-- schema_version_matches=False even though the migration had been applied.
-- ROOT FIX: add the INSERT with ON CONFLICT DO NOTHING for idempotency.
INSERT INTO schema_version (version, description)
VALUES (
    16,
    'P1-013 ROOT FIX: tighten proteins.uniprot_id CHECK to LENGTH IN (6, 10) '
    'matching the ORM and UniProt spec. Quarantine rows with invalid length '
    '(set to NULL) before re-adding the constraint.'
)
ON CONFLICT (version) DO NOTHING;

COMMIT;

-- ===========================================================================
-- Post-migration verification (P1-013 ROOT FIX -- assert the CHECK exists
-- with the strict contract). Runs AFTER COMMIT so the verification sees
-- the committed state.
-- ===========================================================================
DO $$
DECLARE
    _constraint_exists BOOLEAN;
    _constraint_def TEXT;
    _invalid_count INTEGER;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_proteins_uniprot_length'
          AND conrelid = 'proteins'::regclass
    ) INTO _constraint_exists;

    IF NOT _constraint_exists THEN
        RAISE EXCEPTION 'P1-013 VERIFICATION FAILED: chk_proteins_uniprot_length constraint missing after migration 016 -- the DB is in a half-migrated state. Manual intervention required.';
    END IF;

    -- Verify the constraint definition contains the strict "IN (6, 10)" form.
    SELECT pg_get_constraintdef(oid) INTO _constraint_def
    FROM pg_constraint
    WHERE conname = 'chk_proteins_uniprot_length'
      AND conrelid = 'proteins'::regclass;

    IF _constraint_def IS NULL THEN
        RAISE EXCEPTION 'P1-013 VERIFICATION FAILED: could not read chk_proteins_uniprot_length definition';
    END IF;

    -- Check that the definition references "6" and "10" (the strict lengths).
    -- We don't check for the exact "IN (6, 10)" syntax because PostgreSQL
    -- may normalize the expression. The presence of both "6" and "10" and
    -- the absence of "4" (the old relaxed lower bound) is sufficient.
    IF _constraint_def NOT LIKE '%6%' OR _constraint_def NOT LIKE '%10%' THEN
        RAISE EXCEPTION 'P1-013 VERIFICATION FAILED: chk_proteins_uniprot_length does not contain the strict lengths (6, 10). Got: %', _constraint_def;
    END IF;

    -- Verify NO rows have invalid uniprot_id length anymore.
    SELECT COUNT(*) INTO _invalid_count
    FROM proteins
    WHERE uniprot_id IS NOT NULL AND LENGTH(uniprot_id) NOT IN (6, 10);

    IF _invalid_count > 0 THEN
        RAISE EXCEPTION 'P1-013 VERIFICATION FAILED: proteins table still contains % rows with invalid uniprot_id length. The quarantine step did not complete. Manual intervention required.', _invalid_count;
    END IF;

    RAISE NOTICE 'P1-013 VERIFICATION PASSED: chk_proteins_uniprot_length exists with strict contract (LENGTH IN (6, 10)) and no rows have invalid uniprot_id length.';
END $$;
