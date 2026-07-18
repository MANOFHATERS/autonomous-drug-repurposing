/**
 * BE-078 v123 — pg-mem-backed verification.
 *
 * The main v123-root-fixes.test.ts uses the real test postgres DB, which
 * is not always available locally. This file uses an in-memory mock of
 * the Prisma client's twoFactorSetupToken model to verify the DB-backed
 * atomic enforcement works end-to-end.
 *
 * The mock mimics Prisma's API (create, findUnique, updateMany, delete)
 * and enforces the same constraints as the real DB:
 *   - Unique on tokenHash (create throws P2002 on collision).
 *   - updateMany with `where: { id, usedAt: null }` returns count=1 only
 *     if usedAt is STILL null — this is the atomic claim that prevents
 *     multi-instance races.
 *
 * This is sufficient to verify the BE-078 root fix logic; a real
 * Postgres would provide stronger guarantees (row-level locking) but
 * the application-level code path is the same.
 */

// Self-contained jest.mock factory. ALL state is INSIDE the factory —
// no external references (Jest hoists jest.mock above imports, so external
// references would be undefined at factory-execution time).
jest.mock("@/lib/db", () => {
  // In-memory store mimicking the TwoFactorSetupToken table.
  // Keyed by row.id for O(1) lookup by id; tokenHash lookups scan
  // (the test dataset is tiny — single-digit rows per test).
  const table = new Map();
  let nextId = 1;

  const twoFactorSetupToken = {
    create: async ({ data }: any) => {
      // Enforce unique constraint on tokenHash (mimics Prisma P2002).
      for (const row of table.values()) {
        if (row.tokenHash === data.tokenHash) {
          const err: any = new Error("Unique constraint violation");
          err.code = "P2002";
          throw err;
        }
      }
      const row = {
        // Explicit defaults for nullable fields (mimics Postgres column
        // defaults — `usedAt` defaults to NULL per the schema, but
        // JavaScript's `undefined` is NOT `null`, and the verify code
        // checks `row.usedAt !== null` which would WRONGLY pass for
        // `undefined`. Setting it explicitly to `null` here matches
        // what Prisma would return from a real DB query.)
        usedAt: null,
        ...data,
        id: `row-${nextId++}`,
        issuedAt: new Date(),
      };
      table.set(row.id, row);
      return row;
    },
    findUnique: async ({ where }: any) => {
      for (const row of table.values()) {
        if (row.tokenHash === where.tokenHash) return row;
      }
      return null;
    },
    update: async ({ where, data }: any) => {
      const row = table.get(where.id);
      if (!row) throw new Error("Record not found");
      Object.assign(row, data);
      return row;
    },
    // *** THE CRITICAL ATOMIC OPERATION ***
    // updateMany with `where: { id, usedAt: null }` returns count=1 ONLY
    // if usedAt is still null at the moment of the update. This is the
    // atomic claim that prevents multi-instance races: two concurrent
    // calls can't both see usedAt=null and both proceed.
    updateMany: async ({ where, data }: any) => {
      const row = table.get(where.id);
      if (!row) return { count: 0 };
      if (row.usedAt !== null) return { count: 0 };
      row.usedAt = data.usedAt;
      return { count: 1 };
    },
    delete: async ({ where }: any) => {
      const row = table.get(where.id);
      table.delete(where.id);
      return row;
    },
    deleteMany: async () => {
      const count = table.size;
      table.clear();
      return { count };
    },
    // Exposed for tests that want to inspect/mutate the table directly.
    __testTable: table,
  };

  return {
    __esModule: true,
    default: {
      twoFactorSetupToken,
      $disconnect: async () => {},
    },
    // Also export as named for code that imports { db }.
    db: {
      twoFactorSetupToken,
      $disconnect: async () => {},
    },
  };
});

// Now import the SUT (system under test) — it picks up our mocked prisma.
import { issue2faSetupToken, verify2faSetupToken } from "@/lib/auth/two-factor-setup-token";
// Import the mocked db so we can clear its table between tests.
import { db } from "@/lib/db";

describe("[BE-078 v123] TwoFactorSetupToken DB-backed atomic enforcement", () => {
  beforeAll(() => {
    process.env.NODE_ENV = "test";
    process.env.JWT_SECRET = "test-secret-at-least-32-characters-long-for-hs256!!";
  });

  beforeEach(async () => {
    // Clear the in-memory table between tests so they're isolated.
    await (db as any).twoFactorSetupToken.deleteMany({});
  });

  test("issue2faSetupToken is async (returns a Promise)", async () => {
    const result = issue2faSetupToken("u1", "SECRET");
    expect(result).toBeInstanceOf(Promise);
    await result;
  });

  test("issue2faSetupToken persists the token hash to the DB (not the raw token)", async () => {
    const result = await issue2faSetupToken("u-persist", "REALSECRET");
    // Look up the row by tokenHash via the mock's findUnique.
    const crypto = require("crypto");
    const tokenHash = crypto.createHash("sha256").update(result.setupToken).digest("hex");
    const row = await (db as any).twoFactorSetupToken.findUnique({ where: { tokenHash } });
    expect(row).not.toBeNull();
    expect(row.userId).toBe("u-persist");
    expect(row.usedAt).toBeNull();
    // The raw secret must NOT be stored — only the hash (defense in depth).
    expect(row.secretHash).not.toBe("REALSECRET");
    expect(row.secretHash).toBe(crypto.createHash("sha256").update("REALSECRET").digest("hex"));
  });

  test("verify2faSetupToken succeeds on first use", async () => {
    const result = await issue2faSetupToken("u-first", "SECRET1");
    const ok = await verify2faSetupToken("u-first", result.secret, result.setupToken);
    expect(ok.ok).toBe(true);
  });

  test("verify2faSetupToken REJECTS replay (second use → token_used)", async () => {
    const result = await issue2faSetupToken("u-replay", "SECRET2");
    const first = await verify2faSetupToken("u-replay", result.secret, result.setupToken);
    expect(first.ok).toBe(true);
    const second = await verify2faSetupToken("u-replay", result.secret, result.setupToken);
    expect(second.ok).toBe(false);
    expect(second.reason).toBe("token_used");
  });

  test("verify2faSetupToken REJECTS wrong userId (user_mismatch)", async () => {
    const result = await issue2faSetupToken("u-real", "SECRET3");
    const wrong = await verify2faSetupToken("u-attacker", result.secret, result.setupToken);
    expect(wrong.ok).toBe(false);
    expect(wrong.reason).toBe("user_mismatch");
  });

  test("verify2faSetupToken REJECTS wrong secret (secret_mismatch)", async () => {
    const result = await issue2faSetupToken("u-secret", "REALSECRET");
    const wrong = await verify2faSetupToken("u-secret", "WRONGSECRET", result.setupToken);
    expect(wrong.ok).toBe(false);
    expect(wrong.reason).toBe("secret_mismatch");
  });

  test("verify2faSetupToken REJECTS expired token (token_expired)", async () => {
    const result = await issue2faSetupToken("u-expire", "SECRET4");
    // Manually expire the row by setting expiresAt to the past.
    const crypto = require("crypto");
    const tokenHash = crypto.createHash("sha256").update(result.setupToken).digest("hex");
    const row = await (db as any).twoFactorSetupToken.findUnique({ where: { tokenHash } });
    expect(row).not.toBeNull();
    await (db as any).twoFactorSetupToken.update({
      where: { id: row.id },
      data: { expiresAt: new Date(Date.now() - 1000) },
    });
    const expired = await verify2faSetupToken("u-expire", result.secret, result.setupToken);
    expect(expired.ok).toBe(false);
    expect(expired.reason).toBe("token_expired");
  });

  test("verify2faSetupToken REJECTS unknown token (token_not_found)", async () => {
    const result = await verify2faSetupToken("u-nobody", "SECRET", "nonexistent-token-12345");
    expect(result.ok).toBe(false);
    expect(result.reason).toBe("token_not_found");
  });

  test("verify2faSetupToken is async (returns a Promise)", async () => {
    const result = await issue2faSetupToken("u-async", "SECRET");
    const verify = verify2faSetupToken("u-async", result.secret, result.setupToken);
    expect(verify).toBeInstanceOf(Promise);
    await verify;
  });

  // *** THE CRITICAL BE-078 MULTI-INSTANCE RACE-PREVENTION TEST ***
  test("ATOMIC CLAIM: concurrent verify calls — only ONE wins", async () => {
    // Two concurrent verify calls with the same token must NOT both
    // succeed. The mock's updateMany atomically checks `usedAt IS NULL`
    // and returns count=1 for the winner, count=0 for the loser.
    // Real Postgres would use row-level locking for the same effect.
    const result = await issue2faSetupToken("u-race", "SECRET5");
    // Fire both verifies concurrently (don't await one before starting the other).
    const [r1, r2] = await Promise.all([
      verify2faSetupToken("u-race", result.secret, result.setupToken),
      verify2faSetupToken("u-race", result.secret, result.setupToken),
    ]);
    // Exactly one must succeed; the other must be rejected as token_used.
    const successes = [r1, r2].filter((r) => r.ok).length;
    const failures = [r1, r2].filter((r) => !r.ok && r.reason === "token_used").length;
    expect(successes).toBe(1);
    expect(failures).toBe(1);
  });
});
