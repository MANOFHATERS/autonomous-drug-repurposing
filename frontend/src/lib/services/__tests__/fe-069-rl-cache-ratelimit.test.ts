/**
 * FE-069 ROOT FIX tests: /api/rl CSV cache + per-user rate limiting.
 *
 * These tests verify:
 *   1. parseRlCsvContent correctly maps CSV rows to RlCandidate shape.
 *   2. filterAndRankCandidates sorts by overallScore desc, reassigns rank,
 *      applies drug/disease filters, and slices to limit.
 *   3. readRlCsvCached returns cached result on second call (no re-parse).
 *   4. File watcher invalidates cache when the CSV changes.
 *   5. checkUserRateLimit blocks the 61st request within 60s and returns
 *      a Retry-After value.
 *
 * These tests run WITHOUT a database — they exercise the pure cache +
 * rate-limit modules directly.
 */

import { promises as fs } from "fs";
import * as path from "path";
import * as os from "os";
import {
  parseRlCsvContent,
  filterAndRankCandidates,
  readRlCsvCached,
  __clearRlCsvCacheForTests,
} from "@/lib/services/rl-csv-cache";
import {
  checkUserRateLimit,
  // FE-017: sync aliases so this existing test suite keeps working without
  // rewriting every beforeEach/test to be async.
  resetUserRateLimitSync as resetUserRateLimit,
  __clearAllUserRateLimitsForTestsSync as __clearAllUserRateLimitsForTests,
} from "@/lib/auth/per-user-rate-limit";

describe("FE-069: /api/rl CSV cache", () => {
  const SAMPLE_CSV = `drug,disease,gnn_score,safety_score,market_score,reward,rank,policy_prob,literature_support,is_known_positive,confidence,pathway_score,unmet_need_score,efficacy_score,adme_score
metformin,alzheimer,0.85,0.92,0.78,0.83,1,0.71,true,false,0.80,0.82,0.90,0.75,0.88
aspirin,migraine,0.72,0.65,0.55,0.62,2,0.58,false,false,0.70,0.68,0.60,0.65,0.70
losartan,diabetes,0.91,0.95,0.82,0.89,3,0.78,true,true,0.88,0.85,0.92,0.80,0.90
`;

  test("parseRlCsvContent maps every CSV column to the RlCandidate schema", () => {
    const candidates = parseRlCsvContent(SAMPLE_CSV);
    expect(candidates.length).toBe(3);

    const c = candidates[0];
    expect(c.drug).toBe("metformin");
    expect(c.disease).toBe("alzheimer");
    expect(c.plausibilityScore).toBeCloseTo(0.85);
    expect(c.safetyScore).toBeCloseTo(0.92);
    expect(c.marketScore).toBeCloseTo(0.78);
    expect(c.reward).toBeCloseTo(0.83);
    expect(c.policyProb).toBeCloseTo(0.71);
    expect(c.literatureSupport).toBe(true);
    expect(c.isKnownPositive).toBe(false);
    // overall = 0.4*gnn + 0.3*safety + 0.3*market
    const expectedOverall = 0.4 * 0.85 + 0.3 * 0.92 + 0.3 * 0.78;
    expect(c.overallScore).toBeCloseTo(expectedOverall, 5);
  });

  test("filterAndRankCandidates sorts by overallScore desc and reassigns ranks", () => {
    const candidates = parseRlCsvContent(SAMPLE_CSV);
    const ranked = filterAndRankCandidates(candidates, {});
    expect(ranked[0].drug).toBe("losartan"); // highest overallScore
    expect(ranked[0].rank).toBe(1);
    expect(ranked[1].drug).toBe("metformin");
    expect(ranked[1].rank).toBe(2);
    expect(ranked[2].drug).toBe("aspirin");
    expect(ranked[2].rank).toBe(3);
  });

  test("filterAndRankCandidates applies drug filter", () => {
    const candidates = parseRlCsvContent(SAMPLE_CSV);
    const filtered = filterAndRankCandidates(candidates, { drug: "asp" });
    expect(filtered.length).toBe(1);
    expect(filtered[0].drug).toBe("aspirin");
  });

  test("filterAndRankCandidates applies limit after sort", () => {
    const candidates = parseRlCsvContent(SAMPLE_CSV);
    const limited = filterAndRankCandidates(candidates, { limit: 2 });
    expect(limited.length).toBe(2);
    expect(limited[0].rank).toBe(1);
    expect(limited[1].rank).toBe(2);
  });

  test("readRlCsvCached returns cached result on second call (no disk re-read)", async () => {
    __clearRlCsvCacheForTests();
    const tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "rl-csv-"));
    const csvPath = path.join(tmpDir, "rl.csv");
    await fs.writeFile(csvPath, SAMPLE_CSV);

    try {
      const first = await readRlCsvCached(csvPath);
      expect(first.length).toBe(3);
      // Mutate the file's mtime to a past time so the cache check is
      // unambiguous — we want to confirm the second call returns the
      // CACHED array (same reference), not a freshly-parsed one.
      const second = await readRlCsvCached(csvPath);
      // Cache hit: the SAME array reference is returned (no re-parse).
      expect(second).toBe(first);
    } finally {
      await fs.rm(tmpDir, { recursive: true, force: true });
      __clearRlCsvCacheForTests();
    }
  });

  test("readRlCsvCached invalidates cache when CSV mtime changes", async () => {
    __clearRlCsvCacheForTests();
    const tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "rl-csv-"));
    const csvPath = path.join(tmpDir, "rl.csv");
    await fs.writeFile(csvPath, SAMPLE_CSV);

    try {
      const first = await readRlCsvCached(csvPath);
      expect(first.length).toBe(3);

      // Wait >1s so the mtime actually changes (filesystem mtime granularity
      // is often 1s on Linux ext4, ~100ms on macOS APFS).
      await new Promise((r) => setTimeout(r, 1100));

      const NEW_CSV = SAMPLE_CSV + "ritonavir,hiv,0.99,0.88,0.95,0.91,4,0.82,true,true,0.90,0.88,0.95,0.85,0.92\n";
      await fs.writeFile(csvPath, NEW_CSV);

      const second = await readRlCsvCached(csvPath);
      // Cache should have been invalidated → new array with 4 rows.
      expect(second).not.toBe(first);
      expect(second.length).toBe(4);
      expect(second[3].drug).toBe("ritonavir");
    } finally {
      await fs.rm(tmpDir, { recursive: true, force: true });
      __clearRlCsvCacheForTests();
    }
  });
});

describe("FE-069: per-user rate limiting for /api/rl", () => {
  const USER_ID = "clxxxxxxxxxxxxxxxxxxxx01";

  beforeEach(() => {
    __clearAllUserRateLimitsForTests();
  });

  test("allows up to `max` requests within the window", () => {
    for (let i = 0; i < 60; i++) {
      const rl = checkUserRateLimit(USER_ID, { max: 60, windowSeconds: 60 });
      expect(rl.blocked).toBe(false);
    }
  });

  test("blocks the 61st request within the window with a positive retryAfterSeconds", () => {
    for (let i = 0; i < 60; i++) {
      checkUserRateLimit(USER_ID, { max: 60, windowSeconds: 60 });
    }
    const rl = checkUserRateLimit(USER_ID, { max: 60, windowSeconds: 60 });
    expect(rl.blocked).toBe(true);
    expect(rl.retryAfterSeconds).toBeGreaterThan(0);
    expect(rl.retryAfterSeconds).toBeLessThanOrEqual(60);
    expect(rl.remaining).toBe(0);
  });

  test("rate limit is per-user: a second user is not affected", () => {
    const USER_A = "clxxxxxxxxxxxxxxxxxxxx02";
    const USER_B = "clxxxxxxxxxxxxxxxxxxxx03";
    for (let i = 0; i < 60; i++) {
      checkUserRateLimit(USER_A, { max: 60, windowSeconds: 60 });
    }
    const aBlocked = checkUserRateLimit(USER_A, { max: 60, windowSeconds: 60 });
    const bOk = checkUserRateLimit(USER_B, { max: 60, windowSeconds: 60 });
    expect(aBlocked.blocked).toBe(true);
    expect(bOk.blocked).toBe(false);
  });

  test("resetUserRateLimit clears the user's bucket", () => {
    for (let i = 0; i < 60; i++) {
      checkUserRateLimit(USER_ID, { max: 60, windowSeconds: 60 });
    }
    expect(checkUserRateLimit(USER_ID, { max: 60, windowSeconds: 60 }).blocked).toBe(true);
    resetUserRateLimit(USER_ID);
    const after = checkUserRateLimit(USER_ID, { max: 60, windowSeconds: 60 });
    expect(after.blocked).toBe(false);
    expect(after.remaining).toBe(59);
  });
});
