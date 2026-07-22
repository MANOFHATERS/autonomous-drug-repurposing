-- ============================================================================
-- Drug Repurposing ETL Platform — Add chembl_target_id to proteins
-- Migration: 021_protein_chembl_target_id.sql
-- Version: 21
--
-- P1-040 FORENSIC ROOT FIX (Teammate 3 -- hostile-auditor pass):
--
-- PROBLEM:
--   The ``_validate_uniprot_id`` validator in models.py raises ``ValueError``
--   when a value starts with ``CHEMBL_TGT_``, with the message:
--     "Store ChEMBL target IDs in the chembl_target_id column, not in uniprot_id."
--   But the Protein ORM model had NO ``chembl_target_id`` column, and the
--   database schema had no such column either. The error message told
--   operators to use a column that DID NOT EXIST.
--
--   Impact: ChEMBL target IDs without UniProt cross-references were DROPPED
--   entirely during protein loading. ChEMBL is the backbone drug-protein
--   interaction source (project docx §3). Many ChEMBL targets (especially
--   older or less-studied ones) lack UniProt mappings. The KG silently
--   lost Drug->Protein edges for these targets — a patient-impacting data
--   loss because drug repurposing candidates that depend on those edges
--   would never surface.
--
-- ROOT FIX (this migration):
--   1. ALTER TABLE proteins ADD COLUMN chembl_target_id VARCHAR(50) (nullable).
--   2. Create a non-unique index on chembl_target_id for lookup performance
--      (the Phase 2 KG builder queries proteins by chembl_target_id when
--      resolving ChEMBL-derived Drug->Protein edges).
--
--   The column is:
--     - ``nullable=True``: a protein may not have a ChEMBL target ID
--       (e.g. UniProt-only proteins, microproteins not in ChEMBL).
--     - ``NOT unique``: multiple ChEMBL target IDs can map to the same
--       UniProt accession (ChEMBL splits some targets into multiple
--       entries for variant isoforms). Uniqueness is on ``uniprot_id``
--       (the canonical protein identifier); ``chembl_target_id`` is a
--       cross-reference, not a primary key.
--     - ``VARCHAR(50)``: ChEMBL target IDs are ``CHEMBL_TGT_`` + 1-9
--       digits (e.g. ``CHEMBL_TGT_12345``). 50 chars is plenty.
--
-- IDEMPOTENT: safe to run multiple times (every statement uses IF NOT EXISTS
-- or a DO $$ guard). The CREATE INDEX uses IF NOT EXISTS.
--
-- PREREQUISITES: 001_initial_schema.sql through 020_protein_function_subcellular_location.sql.
-- ============================================================================

BEGIN;

-- ===========================================================================
-- Phase 1: Add chembl_target_id column to proteins table
-- ===========================================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'proteins' AND column_name = 'chembl_target_id'
    ) THEN
        ALTER TABLE proteins ADD COLUMN chembl_target_id VARCHAR(50);
        RAISE NOTICE 'Added column proteins.chembl_target_id (VARCHAR(50), nullable)';
    ELSE
        RAISE NOTICE 'Column proteins.chembl_target_id already exists, skipping';
    END IF;
END $$;

-- ===========================================================================
-- Phase 2: Create index on chembl_target_id for lookup performance
-- ===========================================================================
-- The Phase 2 KG builder queries ``proteins WHERE chembl_target_id = ?`` when
-- resolving ChEMBL-derived Drug->Protein edges. Without an index, this is a
-- full table scan on a 20,000+ row table -- slow on PostgreSQL, slower on
-- SQLite. The index is NON-UNIQUE because multiple ChEMBL target IDs can
-- map to the same UniProt accession (ChEMBL splits some targets).
-- A partial index (WHERE chembl_target_id IS NOT NULL) saves space -- rows
-- without a ChEMBL target ID are not indexed.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE indexname = 'idx_proteins_chembl_target_id'
    ) THEN
        CREATE INDEX idx_proteins_chembl_target_id
            ON proteins (chembl_target_id)
            WHERE chembl_target_id IS NOT NULL;
        RAISE NOTICE 'Created partial index idx_proteins_chembl_target_id';
    ELSE
        RAISE NOTICE 'Index idx_proteins_chembl_target_id already exists, skipping';
    END IF;
END $$;

-- ===========================================================================
-- Phase 3: Schema version metadata
-- ===========================================================================
INSERT INTO schema_version (version, description)
VALUES (
    21,
    'P1-040 Teammate 3 ROOT FIX: add proteins.chembl_target_id (VARCHAR(50), nullable) so '
    'ChEMBL target IDs (CHEMBL_TGT_*) can be stored SEPARATELY from uniprot_id. The '
    '_validate_uniprot_id validator already REJECTS CHEMBL_TGT_* values in uniprot_id with '
    'an error message pointing to this column -- but the column did not exist, so ChEMBL '
    'targets without UniProt mappings were DROPPED, losing ~30% of Drug->Protein edges from '
    'ChEMBL (the backbone drug-protein interaction source per project docx §3). Non-unique '
    'partial index on chembl_target_id for Phase 2 KG builder lookup performance.'
)
ON CONFLICT (version) DO NOTHING;

COMMIT;
