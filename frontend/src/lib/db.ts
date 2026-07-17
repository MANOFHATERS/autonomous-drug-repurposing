import { PrismaClient } from '@prisma/client'

const globalForPrisma = globalThis as unknown as {
  prisma: PrismaClient | undefined
}

// In test mode we always create a fresh client so each test process picks
// up the test DATABASE_URL set by tests/api/setup.ts.
const shouldUseGlobalCache = process.env.NODE_ENV !== 'production' && process.env.NODE_ENV !== 'test'

export const db =
  (shouldUseGlobalCache ? globalForPrisma.prisma : undefined) ??
  new PrismaClient({
    // BE-033 ROOT FIX (Teammate 13, LOW): the previous version had a
    // ternary on NODE_ENV === "test" where BOTH branches returned the
    // IDENTICAL `{ datasources: { db: { url: process.env.DATABASE_URL } } }`
    // object — the ternary did nothing (dead code that misled readers
    // into thinking there was a test-specific config). Root fix: collapse
    // to a single unconditional PrismaClient construction. If a future
    // test needs different options (e.g. `log: ['query']`), add an
    // explicit, DIFFERENT branch then — do not reintroduce an identical
    // ternary.
    datasources: { db: { url: process.env.DATABASE_URL } },
  })

if (shouldUseGlobalCache) globalForPrisma.prisma = db