import { PrismaClient } from '@prisma/client'

/**
 * ROOT FIX for FE-031 (Prisma `log: ['query']` leaks PII in dev logs).
 *
 * Previously: in non-test, non-production mode the PrismaClient was
 * constructed with `log: ['query']`. Every SQL query — including queries
 * that contain user emails, project names, hypothesis drug/disease names
 * (PHI/PII in a pharma context) — was printed to stderr. If dev logs were
 * shipped to a log aggregator or shared, PII leaked.
 *
 * ROOT FIX: we log ONLY `warn` and `error` by default. If a developer
 * needs query logging for debugging, they set `DEBUG_PRISMA=true` in their
 * `.env.local` — an opt-in, off-by-default switch.
 *
 * In test mode we still create a fresh client per-process so each test
 * picks up the test DATABASE_URL set by tests/api/setup.ts.
 */

const globalForPrisma = globalThis as unknown as {
  prisma: PrismaClient | undefined
}

const shouldUseGlobalCache = process.env.NODE_ENV !== 'production' && process.env.NODE_ENV !== 'test'

// Build the log config: only `warn` + `error` by default. Add `query` only
// when DEBUG_PRISMA=true is explicitly set.
const prismaLogConfig: ('query' | 'warn' | 'error')[] = ['warn', 'error']
if (process.env.DEBUG_PRISMA === 'true') {
  prismaLogConfig.push('query')
}

// ROOT FIX for FE-027 unblock: previously the ternary produced two
// different option shapes ({datasources:...} vs {log:...}) and TypeScript
// could not narrow the union when passing to `new PrismaClient()`. We now
// build a single options object with both fields optional and let Prisma
// pick whichever applies.
function buildPrismaOptions(): ConstructorParameters<typeof PrismaClient>[0] {
  const opts: ConstructorParameters<typeof PrismaClient>[0] = {};
  if (process.env.NODE_ENV === 'test') {
    opts.datasources = { db: { url: process.env.DATABASE_URL } };
  } else {
    opts.log = prismaLogConfig;
  }
  return opts;
}

export const db =
  (shouldUseGlobalCache ? globalForPrisma.prisma : undefined) ??
  new PrismaClient(buildPrismaOptions())

if (shouldUseGlobalCache) globalForPrisma.prisma = db
