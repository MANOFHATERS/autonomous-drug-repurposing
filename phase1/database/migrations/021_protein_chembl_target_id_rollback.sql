-- ============================================================================
-- Rollback for Migration 021: protein_chembl_target_id
-- ============================================================================

BEGIN;

-- Drop the index first (depends on the column).
DROP INDEX IF EXISTS idx_proteins_chembl_target_id;

-- Drop the column.
ALTER TABLE proteins DROP COLUMN IF EXISTS chembl_target_id;

-- Remove the schema version entry.
DELETE FROM schema_version WHERE version = 21;

COMMIT;
