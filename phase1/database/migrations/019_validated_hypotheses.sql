-- ============================================================================
-- Drug Repurposing ETL Platform — Add validated_hypotheses table
-- Migration: 019_validated_hypotheses.sql
-- Version: 19
--
-- TM3 Task 3.3 ROOT FIX (v127) — PostgreSQL-backed data flywheel writeback.
--
-- PROBLEM (hostile-auditor finding):
--   The DOCX §10 (Data Flywheel) mandates: "Pharma partners validate a
--   hypothesis (in wet lab or clinical study). This validation result is
--   fed back into the platform as a new labeled data point. The model
--   retrains on this new proprietary data."
--
--   Prior to this migration, Phase 4's ``rl/service.py`` (line 873) called
--   ``write_validated_hypothesis()`` which wrote ONLY to a CSV file at
--   ``phase1/processed_data/validated_hypotheses.csv`` (per the
--   ``shared/contracts/writeback.py`` contract owned by TM14). The CSV is
--   a transport format — ephemeral, not queryable, no FK integrity, no
--   transactional guarantees. A wet-lab-validated drug-disease hypothesis
--   (the platform's core proprietary moat) was being persisted to a flat
--   file that any operator could ``rm``.
--
-- ROOT FIX (this migration):
--   Create the ``validated_hypotheses`` PostgreSQL table as the AUTHORITATIVE
--   durable store for the data flywheel. The CSV (TM14's contract) remains
--   the transport format; this table is the canonical store. The new
--   ``POST /datasets/validated_hypotheses`` endpoint in phase1/service.py
--   (added in this same commit) writes here.
--
-- SCHEMA — 10-column canonical (per TM3 Task 3.3 spec):
--   drug_id        VARCHAR(64)  — canonical drug ID (InChIKey/DrugBank ID/ChEMBL ID)
--   drug_name      VARCHAR(500) — human-readable drug name (denormalized for query)
--   disease_id     VARCHAR(64)  — canonical disease ID (MeSH/DO/OMIM ID)
--   disease_name   VARCHAR(500) — human-readable disease name
--   score          NUMERIC(6,4) — Graph Transformer prediction score (0.0-1.0)
--   outcome        VARCHAR(32)  — validation outcome (see CHECK below)
--   validated_at   TIMESTAMPTZ  — when the wet-lab/clinical validation occurred
--   validated_by   VARCHAR(200) — who validated (pharma partner / study PI)
--   source         VARCHAR(200) — validation source (study ID, registry, etc.)
--   notes          TEXT         — free-form notes
--
--   Plus: id (PK, from IDMixin), created_at, updated_at (from TimestampMixin).
--   Total user-visible columns: 10 + 3 system = 13. The 10 canonical columns
--   match the Task 3.3 spec exactly.
--
-- DESIGN NOTES:
--   - drug_id and disease_id are NULLable VARCHARs (no FK to drugs/
--     gene_disease_associations) because:
--       (a) The writeback CSV (TM14 contract) uses bare drug/disease NAMES,
--           not IDs — the endpoint must look up the ID from the drugs table
--           and may legitimately not find it (e.g. a newly-validated drug
--           not yet loaded). Forcing NOT NULL would reject legitimate
--           writebacks.
--       (b) A FK would couple the validated_hypotheses table to the drugs
--           table's load order — if drugs are re-loaded (TRUNCATE), the
--           FK would cascade-delete validated hypotheses (data-loss hazard).
--           Denormalizing drug_id + drug_name preserves the hypothesis
--           permanently even if the drugs table is rebuilt.
--   - score uses NUMERIC(6,4) (not FLOAT) for precision — GT scores are
--     0.0000-1.0000 with 4 decimal places. NUMERIC avoids float rounding
--     errors that would compound across retraining iterations.
--   - outcome has a CHECK constraint matching the TM14 writeback contract's
--     VALID_OUTCOMES list (validated_positive / validated_toxic /
--     validated_negative / invalidated). This is defense-in-depth: even if
--     the application layer fails to validate, the DB rejects bad outcomes.
--   - Unique constraint on (drug_id, disease_id, validated_at) prevents
--     duplicate writebacks for the same hypothesis at the same timestamp.
--     NULL drug_id/disease_id rows are excluded from the unique constraint
--     (NULL != NULL in SQL) — this is intentional (multiple unnamed
--     hypotheses can coexist).
--
-- PREREQUISITES: 001_initial_schema.sql through
--                018_add_standard_relation_and_is_homodimer.sql.
-- IDEMPOTENT: safe to run multiple times (every statement uses IF NOT EXISTS).
-- ============================================================================

BEGIN;

-- ===========================================================================
-- Phase 1: Create validated_hypotheses table
-- ===========================================================================
CREATE TABLE IF NOT EXISTS validated_hypotheses (
    -- System columns (match IDMixin + TimestampMixin in database/base.py)
    id                              SERIAL PRIMARY KEY,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Canonical 10-column schema (TM3 Task 3.3 spec)
    drug_id                         VARCHAR(64),
    drug_name                       VARCHAR(500) NOT NULL,
    disease_id                      VARCHAR(64),
    disease_name                    VARCHAR(500) NOT NULL,
    score                           NUMERIC(6, 4),
    outcome                         VARCHAR(32) NOT NULL,
    validated_at                    TIMESTAMPTZ NOT NULL,
    validated_by                    VARCHAR(200),
    source                          VARCHAR(200),
    notes                           TEXT,

    -- Constraints
    CONSTRAINT chk_vh_outcome CHECK (
        outcome IN (
            'validated_positive',
            'validated_toxic',
            'validated_negative',
            'invalidated'
        )
    ),
    -- Score must be in [0.0, 1.0] if present (GT prediction range).
    -- NUMERIC(6,4) allows up to 99.9999 but the CHECK clamps to [0,1].
    CONSTRAINT chk_vh_score_range CHECK (
        score IS NULL OR (score >= 0.0 AND score <= 1.0)
    ),
    -- drug_name and disease_name must be non-empty (CHECK约束不能在NOT NULL
    -- 上加更多限制，但可以防止empty string). Empty strings are rejected.
    CONSTRAINT chk_vh_drug_name_nonempty CHECK (
        drug_name IS NOT NULL AND btrim(drug_name) <> ''
    ),
    CONSTRAINT chk_vh_disease_name_nonempty CHECK (
        disease_name IS NOT NULL AND btrim(disease_name) <> ''
    )
);

-- ===========================================================================
-- Phase 2: Indexes
-- ===========================================================================
-- Lookup by drug_id (the canonical query path for "give me all validated
-- hypotheses for drug X"). Partial index — only rows where drug_id is NOT
-- NULL (NULLs would bloat the index without query benefit).
CREATE INDEX IF NOT EXISTS idx_vh_drug_id
    ON validated_hypotheses (drug_id)
    WHERE drug_id IS NOT NULL;

-- Lookup by disease_id (reverse query: "all validated hypotheses for disease Y").
CREATE INDEX IF NOT EXISTS idx_vh_disease_id
    ON validated_hypotheses (disease_id)
    WHERE disease_id IS NOT NULL;

-- Lookup by outcome (the trainer filters "WHERE outcome = 'validated_positive'").
CREATE INDEX IF NOT EXISTS idx_vh_outcome
    ON validated_hypotheses (outcome);

-- Lookup by validated_at (chronological scan for retraining window).
CREATE INDEX IF NOT EXISTS idx_vh_validated_at
    ON validated_hypotheses (validated_at DESC);

-- Unique constraint: prevent duplicate writebacks for the same
-- (drug_id, disease_id, validated_at) triple. NULLs in any of these
-- columns exempt the row from uniqueness (NULL != NULL in SQL).
-- This is intentional: a writeback with unknown drug_id is still
-- accepted (the operator may fill it in later).
CREATE UNIQUE INDEX IF NOT EXISTS uq_vh_drug_disease_time
    ON validated_hypotheses (drug_id, disease_id, validated_at)
    WHERE drug_id IS NOT NULL AND disease_id IS NOT NULL;

-- ===========================================================================
-- Phase 3: updated_at trigger (IDEM-02)
-- ===========================================================================
-- TimestampMixin uses onupdate in SQLAlchemy, but that does NOT fire for
-- bulk operations. The trigger mirrors the pattern used by other tables
-- (see 001_initial_schema.sql's trg_drugs_updated_at pattern at line 452).
-- The update_updated_at() function is created by 001_initial_schema.sql
-- (line 109). If it doesn't exist, the trigger creation fails loudly at
-- migration time, which is the correct behavior (loud failure > silent drift).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trg_vh_updated_at'
    ) THEN
        CREATE TRIGGER trg_vh_updated_at
            BEFORE UPDATE ON validated_hypotheses
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at();
        RAISE NOTICE 'Created trigger trg_vh_updated_at on validated_hypotheses';
    ELSE
        RAISE NOTICE 'Trigger trg_vh_updated_at already exists on validated_hypotheses, skipping';
    END IF;
END $$;

-- ===========================================================================
-- Phase 4: Schema version metadata
-- ===========================================================================
INSERT INTO schema_version (version, description)
VALUES (
    19,
    'TM3 Task 3.3 v127 ROOT FIX: add validated_hypotheses table (10-column canonical '
    'schema) for PostgreSQL-backed data flywheel writeback. Previously Phase 4 wrote '
    'validated hypotheses to CSV only — this migration makes PostgreSQL the authoritative '
    'durable store, with the CSV remaining as a transport format per the TM14 '
    'shared/contracts/writeback.py contract. The new POST /datasets/validated_hypotheses '
    'endpoint in phase1/service.py writes here.'
)
ON CONFLICT (version) DO NOTHING;

COMMIT;
