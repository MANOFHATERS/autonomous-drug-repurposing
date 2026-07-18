-- BE-002 v123 FORENSIC ROOT FIX (CRITICAL):
--
-- Five schema-declared changes had NO Prisma migration, so
-- `prisma migrate deploy` would silently leave the production DB out of
-- sync with `schema.prisma`. The runtime consequences were severe:
--
--   1. MfaChallenge table MISSING → every 2FA login-verify call to
--      `db.mfaChallenge.create()` (jti replay guard) threw
--      `relation "MfaChallenge" does not exist` → 500 on every 2FA login.
--   2. AuditLogDeadLetter table MISSING → writeAuditLog's fallback path
--      (`db.auditLogDeadLetter.create()`) threw on every non-critical
--      audit failure → audit-log dead-letter was broken (FDA 21 CFR
--      Part 11 compliance violation).
--   3. Hypothesis composite unique (projectId, drugName, diseaseName)
--      MISSING → `db.hypothesis.upsert({ where: { projectId_drugName_diseaseName: ... } })`
--      threw because the compound unique key didn't exist → every RL
--      candidate persistence failed (BE-028 optimization that reduced
--      100 DB round-trips to 2 was DEAD in production — reverted to
--      the N+1 pattern, exhausting the connection pool under V1's
--      100-concurrent-request load).
--   4. UserRole.platformOwner enum value MISSING → registering a user
--      with `role="platformOwner"` (impossible via API but possible via
--      direct DB insert) threw `invalid input value for enum UserRole`
--      because the enum value was never added.
--
-- ROOT FIX: this migration creates ALL the missing schema elements in a
-- single atomic migration. Each element is additive (new table, new
-- constraint, new enum value) — no existing data is modified, no
-- rollback is needed. The migration is idempotent: running it twice is
-- a no-op (CREATE TABLE IF NOT EXISTS, ALTER TYPE ADD VALUE IF NOT
-- EXISTS, CREATE UNIQUE INDEX IF NOT EXISTS).

-- ───────────────────────────────────────────────────────────────────────
-- 1. MfaChallenge table — FE-016 replay-protection table.
-- ───────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS "MfaChallenge" (
    "id"         TEXT         NOT NULL,
    "jti"        TEXT         NOT NULL,
    "userId"     TEXT         NOT NULL,
    "consumedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "expiresAt"  TIMESTAMP(3) NOT NULL,
    CONSTRAINT "MfaChallenge_pkey" PRIMARY KEY ("id")
);

-- Unique constraint on jti (single-use replay guard) — Prisma schema
-- declares `jti String @unique`.
CREATE UNIQUE INDEX IF NOT EXISTS "MfaChallenge_jti_key" ON "MfaChallenge"("jti");
-- Indexes on userId and expiresAt (Prisma schema declares @@index).
CREATE INDEX IF NOT EXISTS "MfaChallenge_userId_idx" ON "MfaChallenge"("userId");
CREATE INDEX IF NOT EXISTS "MfaChallenge_expiresAt_idx" ON "MfaChallenge"("expiresAt");

-- ───────────────────────────────────────────────────────────────────────
-- 2. AuditLogDeadLetter table — BE-003 first-class dead-letter model.
-- ───────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS "AuditLogDeadLetter" (
    "id"              TEXT         NOT NULL,
    "action"          TEXT         NOT NULL,
    "resource"        TEXT,
    "userId"          TEXT,
    "actorName"       TEXT         NOT NULL,
    "metadata"        TEXT         NOT NULL DEFAULT '{}',
    "error"           TEXT         NOT NULL,
    "critical"        BOOLEAN      NOT NULL DEFAULT false,
    "organizationId"  TEXT,
    "createdAt"       TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "AuditLogDeadLetter_pkey" PRIMARY KEY ("id")
);

CREATE INDEX IF NOT EXISTS "AuditLogDeadLetter_action_idx" ON "AuditLogDeadLetter"("action");
CREATE INDEX IF NOT EXISTS "AuditLogDeadLetter_userId_idx" ON "AuditLogDeadLetter"("userId");
CREATE INDEX IF NOT EXISTS "AuditLogDeadLetter_createdAt_idx" ON "AuditLogDeadLetter"("createdAt");
CREATE INDEX IF NOT EXISTS "AuditLogDeadLetter_critical_idx" ON "AuditLogDeadLetter"("critical");
CREATE INDEX IF NOT EXISTS "AuditLogDeadLetter_organizationId_idx" ON "AuditLogDeadLetter"("organizationId");

-- ───────────────────────────────────────────────────────────────────────
-- 3. Hypothesis composite unique (projectId, drugName, diseaseName) —
--    BE-028 root fix enables db.hypothesis.upsert() via the
--    `projectId_drugName_diseaseName` compound key.
-- ───────────────────────────────────────────────────────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS "Hypothesis_projectId_drugName_diseaseName_key"
    ON "Hypothesis"("projectId", "drugName", "diseaseName");

-- ───────────────────────────────────────────────────────────────────────
-- 4. UserRole.platformOwner enum value — BE-002 root fix.
-- ───────────────────────────────────────────────────────────────────────
-- PostgreSQL 12+ supports `ADD VALUE IF NOT EXISTS` for enum types.
-- Without this guard, re-running the migration would error with
-- `ERROR: enum label "platformOwner" already exists`. With the guard,
-- the migration is idempotent.
--
-- Note: ALTER TYPE ... ADD VALUE cannot run inside a transaction block
-- in PostgreSQL < 12. PostgreSQL 12+ allows it. Prisma migrate wraps
-- each migration in a transaction by default; if you're on PG < 12,
-- disable the wrap with `--transaction-mode=none` (Prisma 5.10+).
-- The Docker image (Dockerfile.airflow) uses Postgres 15+, so this is
-- safe.
ALTER TYPE "UserRole" ADD VALUE IF NOT EXISTS 'platformOwner';

-- ───────────────────────────────────────────────────────────────────────
-- 5. BE-048 v123: BillingInvoice.idempotencyKey column + unique index.
-- ───────────────────────────────────────────────────────────────────────
-- The composite unique (organizationId, idempotencyKey) ensures that
-- duplicate POSTs to /api/billing/subscription with the same
-- idempotencyKey cannot create duplicate invoices. PostgreSQL's NULL-
-- distinct semantics allow multiple NULL idempotencyKey rows (legacy
-- callers that don't supply a key), so this migration is backward-
-- compatible.
ALTER TABLE "BillingInvoice" ADD COLUMN IF NOT EXISTS "idempotencyKey" TEXT;

-- The unique index uses COALESCE to enforce uniqueness only for non-NULL
-- keys. Without COALESCE, multiple NULL keys would be treated as distinct
-- (which is what we want for legacy callers). With COALESCE, all NULLs
-- would be treated as equal (which would block legacy callers). So the
-- plain unique index (without COALESCE) is correct — PostgreSQL's NULL-
-- distinct semantics do the right thing.
CREATE UNIQUE INDEX IF NOT EXISTS "BillingInvoice_organizationId_idempotencyKey_key"
    ON "BillingInvoice"("organizationId", "idempotencyKey");
