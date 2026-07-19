-- ============================================================================
-- Drug Repurposing ETL Platform — ROLLBACK for validated_hypotheses table
-- Migration: 019_validated_hypotheses_rollback.sql
--
-- TM3 Task 3.3 v127 ROOT FIX — rollback for migration 019.
--
-- DANGER: This rollback DESTROYS the validated_hypotheses table and all its
-- data. Validated hypotheses are the platform's proprietary moat (DOCX §10)
-- — running this rollback in production is a DATA-LOSS EVENT.
--
-- The rollback exists for:
--   1. CI/test isolation (rebuild a fresh schema between test runs).
--   2. Emergency rollback if migration 019 introduced a critical bug.
--
-- In production, prefer a FORWARD migration (020_*) that fixes the issue
-- rather than rolling back 019.
-- ============================================================================

BEGIN;

-- Drop the trigger first (cannot DROP TABLE while trigger exists on it
-- in some Postgres versions — defensive).
DROP TRIGGER IF EXISTS trg_vh_updated_at ON validated_hypotheses;

-- Drop the indexes (CASCADE not needed — DROP TABLE drops dependent indexes).
-- Explicit drops are belt-and-braces for partial rollback scenarios.
DROP INDEX IF EXISTS uq_vh_drug_disease_time;
DROP INDEX IF EXISTS idx_vh_validated_at;
DROP INDEX IF EXISTS idx_vh_outcome;
DROP INDEX IF EXISTS idx_vh_disease_id;
DROP INDEX IF EXISTS idx_vh_drug_id;

-- Drop the table.
DROP TABLE IF EXISTS validated_hypotheses;

-- Remove the schema_version row so check_migrations() reports version 18
-- (the version before this migration). This allows re-applying 019 cleanly.
DELETE FROM schema_version WHERE version = 19;

COMMIT;
