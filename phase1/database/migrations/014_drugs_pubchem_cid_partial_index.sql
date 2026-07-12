-- ============================================================================
-- Drug Repurposing ETL Platform — PubChem Enrichment Partial Index
-- Migration: 013_drugs_pubchem_cid_partial_index.sql
-- Version: 1
-- Author: Team Cosmic
-- Created: 2026-07-12
--
-- P1-036 ROOT FIX (PubChem full-table-scan on every Sunday run):
--   The PubChem pipeline's download phase queries the ``drugs`` table:
--     SELECT inchikey FROM drugs
--     WHERE pubchem_cid IS NULL AND inchikey IS NOT NULL AND is_deleted = FALSE
--     ORDER BY inchikey ASC;
--   With ~10,000 drugs this is fast (<100ms on PostgreSQL), but for 100K+
--   rows (ChEMBL has ~2M compounds) it becomes a multi-second full-table
--   scan that holds a read lock. Migration 001 only indexes ``inchikey``
--   (UNIQUE), ``chembl_id``, ``drugbank_id``, ``name`` — there is NO
--   index on ``pubchem_cid``, so the ``IS NULL`` predicate cannot use
--   any existing index.
--
--   ROOT FIX: add a PARTIAL INDEX on ``inchikey`` restricted to rows
--   where ``pubchem_cid IS NULL``. The index covers exactly the rows
--   PubChem needs to enrich, so the scan becomes an index-only scan
--   over a small subset (typically <5% of the table after the first
--   successful PubChem run). Partial indexes are supported by both
--   PostgreSQL (since 7.2, 2002) and SQLite (since 3.8.0, 2013).
--
--   The index is IDEMPOTENT (``CREATE INDEX IF NOT EXISTS``) so
--   re-running the migration is safe. The rollback sidecar drops it.
--
-- DIALECT COMPATIBILITY:
--   PostgreSQL: full support (partial indexes since 7.2)
--   SQLite: full support (partial indexes since 3.8.0, 2013)
--   The migration runner's statement splitter handles both.
-- ============================================================================

CREATE INDEX IF NOT EXISTS ix_drugs_pubchem_cid_null_inchikey
    ON drugs (inchikey)
    WHERE pubchem_cid IS NULL;
