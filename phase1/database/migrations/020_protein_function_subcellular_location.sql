-- ============================================================================
-- Drug Repurposing ETL Platform — Add function + subcellular_location to proteins
-- Migration: 020_protein_function_subcellular_location.sql
-- Version: 20
--
-- TM1 Task 1.3 ROOT FIX (v130) — Phase 1 → Phase 3 node-feature contract.
--
-- PROBLEM (hostile-auditor finding):
--   The Phase 1 schema (phase1_schema.py) declares ``function`` and
--   ``subcellular_location`` as optional columns of the ``uniprot_proteins``
--   source, and the UniProt pipeline writes both to the CSV. But the ORM
--   ``Protein`` model declared only ``function_desc`` (legacy name) and
--   did NOT declare ``function`` or ``subcellular_location``. As a result,
--   ``bulk_upsert_proteins`` silently dropped both columns before INSERT
--   (loaders.py does ``df[[c for c in load_columns if c in df.columns]]``
--   where ``load_columns`` is derived from ``Protein.__table__.columns``).
--   The Phase 2 bridge then queried the DB and got NULL for both fields,
--   propagating empty strings into the KG Protein nodes — defeating the
--   Phase 3 node-feature extraction (TASK-141).
--
--   Additionally, the ``sequence`` column was VARCHAR(50000) and the
--   ``function_desc`` column was VARCHAR(10000). Titin (~34,350 aa) fits
--   within 50,000 chars, but the cap is a latent truncation risk for
--   hypothetical proteins > 50,000 aa. UniProt FUNCTION descriptions for
--   multifunctional proteins (e.g. BRCA1, p53) can exceed 10,000 chars.
--
-- ROOT FIX (this migration):
--   1. ALTER TABLE proteins ADD COLUMN function TEXT (nullable).
--   2. ALTER TABLE proteins ADD COLUMN subcellular_location TEXT (nullable).
--   3. ALTER TABLE proteins ALTER COLUMN sequence TYPE TEXT (was VARCHAR(50000)).
--   4. ALTER TABLE proteins ALTER COLUMN function_desc TYPE TEXT (was VARCHAR(10000)).
--
--   The ``function`` column is an alias for ``function_desc`` — kept
--   distinct so downstream consumers that read ``function`` get the
--   value without a rename. The application layer may populate either
--   or both; consumers should treat them as interchangeable.
--
-- IDEMPOTENT: safe to run multiple times (every statement uses IF NOT EXISTS
-- or a DO $$ guard). The ALTER COLUMN TYPE is idempotent because PostgreSQL
-- accepts "ALTER COLUMN x TYPE TEXT" even if the column is already TEXT.
--
-- PREREQUISITES: 001_initial_schema.sql through 019_validated_hypotheses.sql.
-- ============================================================================

BEGIN;

-- ===========================================================================
-- Phase 1: Add new columns to proteins table
-- ===========================================================================

-- ``function`` column — alias for ``function_desc`` (TEXT, nullable).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'proteins' AND column_name = 'function'
    ) THEN
        ALTER TABLE proteins ADD COLUMN function TEXT;
        RAISE NOTICE 'Added column proteins.function (TEXT, nullable)';
    ELSE
        RAISE NOTICE 'Column proteins.function already exists, skipping';
    END IF;
END $$;

-- ``subcellular_location`` column — UniProt cc_subcellular_location (TEXT, nullable).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'proteins' AND column_name = 'subcellular_location'
    ) THEN
        ALTER TABLE proteins ADD COLUMN subcellular_location TEXT;
        RAISE NOTICE 'Added column proteins.subcellular_location (TEXT, nullable)';
    ELSE
        RAISE NOTICE 'Column proteins.subcellular_location already exists, skipping';
    END IF;
END $$;

-- ===========================================================================
-- Phase 2: Widen sequence + function_desc to TEXT (remove truncation risk)
-- ===========================================================================
-- The previous VARCHAR(50000) on sequence and VARCHAR(10000) on function_desc
-- were latent truncation risks. PostgreSQL silently truncates on INSERT if
-- the value exceeds the declared length — meaning a 60,000-aa hypothetical
-- protein would be loaded as a 50,000-aa fragment with no error.
-- TEXT has no length limit on both PostgreSQL and SQLite.

-- Alter sequence column type (idempotent — ALTER TYPE is a no-op if already TEXT).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'proteins'
          AND column_name = 'sequence'
          AND data_type <> 'text'
    ) THEN
        ALTER TABLE proteins ALTER COLUMN sequence TYPE TEXT;
        RAISE NOTICE 'Altered proteins.sequence to TEXT (was VARCHAR)';
    ELSE
        RAISE NOTICE 'proteins.sequence is already TEXT (or column missing), skipping';
    END IF;
END $$;

-- Alter function_desc column type (idempotent).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'proteins'
          AND column_name = 'function_desc'
          AND data_type <> 'text'
    ) THEN
        ALTER TABLE proteins ALTER COLUMN function_desc TYPE TEXT;
        RAISE NOTICE 'Altered proteins.function_desc to TEXT (was VARCHAR)';
    ELSE
        RAISE NOTICE 'proteins.function_desc is already TEXT (or column missing), skipping';
    END IF;
END $$;

-- ===========================================================================
-- Phase 3: Backfill ``function`` from ``function_desc`` for existing rows
-- ===========================================================================
-- For rows that already exist (loaded before this migration), populate
-- ``function`` from ``function_desc`` so both columns are consistent.
-- New INSERTs from the UniProt pipeline will populate both directly.
UPDATE proteins
SET function = function_desc
WHERE function IS NULL AND function_desc IS NOT NULL;

-- ===========================================================================
-- Phase 4: Schema version metadata
-- ===========================================================================
INSERT INTO schema_version (version, description)
VALUES (
    20,
    'TM1 Task 1.3 v130 ROOT FIX: add proteins.function + proteins.subcellular_location '
    'columns (TEXT, nullable) so the UniProt pipeline can persist the fields the Phase 1 '
    'schema declares and Phase 3 node-feature extraction (TASK-141) consumes. Also widen '
    'proteins.sequence and proteins.function_desc from VARCHAR to TEXT to remove latent '
    'truncation risk for proteins > 50,000 aa (e.g. titin) and FUNCTION descriptions > '
    '10,000 chars (e.g. BRCA1, p53). Backfill proteins.function from proteins.function_desc '
    'for existing rows so both columns are consistent.'
)
ON CONFLICT (version) DO NOTHING;

COMMIT;
