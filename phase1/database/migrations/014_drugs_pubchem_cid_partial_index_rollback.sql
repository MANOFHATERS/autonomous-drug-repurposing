-- ============================================================================
-- Rollback for Migration 013: drugs_pubchem_cid_partial_index
-- Drops the partial index added by 013_drugs_pubchem_cid_partial_index.sql.
-- Safe to run multiple times (uses IF EXISTS).
-- ============================================================================

DROP INDEX IF EXISTS ix_drugs_pubchem_cid_null_inchikey;
