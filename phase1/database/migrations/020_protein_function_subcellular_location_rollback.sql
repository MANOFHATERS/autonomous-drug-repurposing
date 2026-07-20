-- ============================================================================
-- Drug Repurposing ETL Platform — ROLLBACK for migration 020
-- Migration: 020_protein_function_subcellular_location_rollback.sql
--
-- TM1 Task 1.3 v130 ROOT FIX — rollback for migration 020.
--
-- DANGER: This rollback DROPS the ``function`` and ``subcellular_location``
-- columns from the ``proteins`` table. Any data in those columns is lost.
-- It also re-introduces the VARCHAR(50000) cap on ``sequence`` and the
-- VARCHAR(10000) cap on ``function_desc`` — meaning any rows with longer
-- values would need to be truncated before this rollback can succeed.
--
-- The rollback exists for:
--   1. CI/test isolation (rebuild a fresh schema between test runs).
--   2. Emergency rollback if migration 020 introduced a critical bug.
--
-- In production, prefer a FORWARD migration (021_*) that fixes the issue
-- rather than rolling back 020.
-- ============================================================================

BEGIN;

-- Drop the new columns (data loss warning in the header comment above).
ALTER TABLE proteins DROP COLUMN IF EXISTS subcellular_location;
ALTER TABLE proteins DROP COLUMN IF EXISTS function;

-- Re-impose the original VARCHAR caps. NOTE: if any existing row's
-- ``sequence`` exceeds 50,000 chars or ``function_desc`` exceeds 10,000
-- chars, this ALTER will FAIL with "value too long for type". The
-- operator must TRUNCATE those rows manually before re-running this
-- rollback. This is intentional — silent truncation is worse than a
-- loud failure.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'proteins'
          AND column_name = 'sequence'
          AND data_type = 'text'
    ) THEN
        ALTER TABLE proteins ALTER COLUMN sequence TYPE VARCHAR(50000);
        RAISE NOTICE 'Re-imposed VARCHAR(50000) cap on proteins.sequence';
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'proteins'
          AND column_name = 'function_desc'
          AND data_type = 'text'
    ) THEN
        ALTER TABLE proteins ALTER COLUMN function_desc TYPE VARCHAR(10000);
        RAISE NOTICE 'Re-imposed VARCHAR(10000) cap on proteins.function_desc';
    END IF;
END $$;

-- Remove the schema_version row so check_migrations() reports version 19
-- (the version before this migration). This allows re-applying 020 cleanly.
DELETE FROM schema_version WHERE version = 20;

COMMIT;
