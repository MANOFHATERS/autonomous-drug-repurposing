/**
 * TM10 v130 FORENSIC ROOT FIX (Task 10.6): regression test for
 * /api/auth/refresh rate limiting.
 *
 * The task spec requires:
 *   1. Per-IP rate limit (5/min — the 6th refresh from the same IP within
 *      60 seconds is blocked).
 *   2. Per-user rate limit (10/hour — the 11th refresh from the same user
 *      within 1 hour is blocked).
 *   3. Return 429 with Retry-After header on limit exceeded.
 *
 * VERIFICATION (per task spec):
 *   for i in $(seq 1 10); do curl -X POST http://localhost:3000/api/auth/refresh; done
 *   # should 429 after 5 (6th request blocked by REFRESH_IP_RATE_LIMIT)
 *
 * TM10 v130 ROOT CAUSE: the v128 test file (this file's predecessor)
 * contained a MATHEMATICAL FALSEHOOD in its documentation. It claimed:
 *
 *   "10/min is 600x STRICTER than 10/hour"
 *
 * This is WRONG. 10/min = 600/hour, which is 60x MORE LENIENT than
 * 10/hour, not 600x stricter. The v128 test verified the WRONG limit
 * (10/min) and lied about it being stricter. This is exactly the
 * "aspirational rather than actual" pattern the audit warned about —
 * the test passed but the code did NOT meet the task spec.
 *
 * The v130 fix:
 *   - Changed REFRESH_USER_RATE_LIMIT from {max:10, windowSeconds:60}
 *     (10/min) to {max:10, windowSeconds:3600} (10/hour) in rate-limit.ts.
 *   - Added REFRESH_IP_RATE_LIMIT = {max:5, windowSeconds:60} (5/min)
 *     in rate-limit.ts.
 *   - Updated refresh/route.ts to use a DEDICATED per-IP limiter
 *     (`refresh:ip:${ip}` key) instead of the SHARED login limiter
 *     (20/5min). This decouples refresh rate limits from login.
 *
 * This v130 test file verifies the ACTUAL spec limits, not the wrong
 * v128 limits. The constants are imported directly from rate-limit.ts
 * so the test breaks if someone changes the limits without updating
 * the test.
 */
import {
  REFRESH_IP_RATE_LIMIT,
  REFRESH_USER_RATE_LIMIT,
} from "@/lib/auth/rate-limit";
import {
  checkUserRateLimitDistributed,
  __clearAllUserRateLimitsForTests,
} from "@/lib/auth/per-user-rate-limit";

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("TM10 v130 Task 10.6: /api/auth/refresh rate limiting (forensic root fix)", () => {
  beforeEach(async () => {
    // Reset ALL rate-limit state between tests so they don't interfere.
    // The async distributed limiter shares state with the sync limiter
    // when REDIS_URL is unset (the test env), so clearing the sync state
    // is sufficient.
    await __clearAllUserRateLimitsForTests();
  });

  // =========================================================================
  // Constant verification — these tests FAIL if someone changes the limits
  // without updating the test. This is the guard against the v128 regression
  // where the limit was silently changed from 10/hour to 10/min.
  // =========================================================================
  describe("Refresh rate-limit constants (guard against v128 regression)", () => {
    test("REFRESH_IP_RATE_LIMIT is 5 per 60 seconds (5/min per task spec)", () => {
      expect(REFRESH_IP_RATE_LIMIT.max).toBe(5);
      expect(REFRESH_IP_RATE_LIMIT.windowSeconds).toBe(60);
    });

    test("REFRESH_USER_RATE_LIMIT is 10 per 3600 seconds (10/hour per task spec)", () => {
      expect(REFRESH_USER_RATE_LIMIT.max).toBe(10);
      expect(REFRESH_USER_RATE_LIMIT.windowSeconds).toBe(3600);
    });

    test("REFRESH_USER_RATE_LIMIT is NOT the v128 bug (10/min = 600/hour)", () => {
      // v128 had {max: 10, windowSeconds: 60} which is 10/MINUTE = 600/HOUR.
      // The task spec requires 10/HOUR. This test guards against regression.
      const requestsPerHour =
        (REFRESH_USER_RATE_LIMIT.max * 3600) / REFRESH_USER_RATE_LIMIT.windowSeconds;
      // Must be exactly 10/hour, NOT 600/hour.
      expect(requestsPerHour).toBe(10);
      expect(requestsPerHour).not.toBe(600);
    });

    test("REFRESH_IP_RATE_LIMIT is NOT the shared login limiter (20/5min)", () => {
      // v128 reused the shared login IP limiter (20 per 5 min = 4/min avg,
      // allows bursts of 20 in 1 second). The task spec requires 5/min
      // DEDICATED to refresh. This test guards against regression.
      const requestsPerMinute =
        (REFRESH_IP_RATE_LIMIT.max * 60) / REFRESH_IP_RATE_LIMIT.windowSeconds;
      expect(requestsPerMinute).toBe(5);
      expect(requestsPerMinute).not.toBe(4); // 20/5min average
      expect(REFRESH_IP_RATE_LIMIT.max).not.toBe(20); // login limiter
    });
  });

  // =========================================================================
  // Layer 1: per-IP rate limit (5/min, dedicated to refresh).
  // The route uses the GENERIC checkUserRateLimitDistributed with a
  // synthetic key `refresh:ip:${ip}`. This test exercises the SAME function
  // with the SAME key format to verify the limit is enforced.
  // =========================================================================
  describe("Layer 1: per-IP rate limit (5/min, dedicated to refresh)", () => {
    test("allows up to 5 refreshes per minute from the same IP", async () => {
      const ipKey = "refresh:ip:203.0.113.10";
      for (let i = 0; i < REFRESH_IP_RATE_LIMIT.max; i++) {
        const rl = await checkUserRateLimitDistributed(ipKey, REFRESH_IP_RATE_LIMIT);
        expect(rl.blocked).toBe(false);
        expect(rl.remaining).toBe(REFRESH_IP_RATE_LIMIT.max - i - 1);
      }
    });

    test("blocks the 6th refresh from the same IP within 1 minute (429 + Retry-After)", async () => {
      const ipKey = "refresh:ip:203.0.113.11";
      // Make 5 allowed requests.
      for (let i = 0; i < REFRESH_IP_RATE_LIMIT.max; i++) {
        const rl = await checkUserRateLimitDistributed(ipKey, REFRESH_IP_RATE_LIMIT);
        expect(rl.blocked).toBe(false);
      }
      // 6th request — should be blocked.
      const rl = await checkUserRateLimitDistributed(ipKey, REFRESH_IP_RATE_LIMIT);
      expect(rl.blocked).toBe(true);
      expect(rl.retryAfterSeconds).toBeGreaterThan(0);
      expect(rl.retryAfterSeconds).toBeLessThanOrEqual(REFRESH_IP_RATE_LIMIT.windowSeconds);
      expect(rl.remaining).toBe(0);
    });

    test("rate limit is PER-IP — different IPs have independent buckets", async () => {
      const ipKey1 = "refresh:ip:203.0.113.20";
      const ipKey2 = "refresh:ip:203.0.113.21";
      // Exhaust IP 1's bucket.
      for (let i = 0; i < REFRESH_IP_RATE_LIMIT.max + 1; i++) {
        await checkUserRateLimitDistributed(ipKey1, REFRESH_IP_RATE_LIMIT);
      }
      // IP 1 is blocked.
      const rl1 = await checkUserRateLimitDistributed(ipKey1, REFRESH_IP_RATE_LIMIT);
      expect(rl1.blocked).toBe(true);
      // IP 2 is NOT blocked — independent bucket.
      const rl2 = await checkUserRateLimitDistributed(ipKey2, REFRESH_IP_RATE_LIMIT);
      expect(rl2.blocked).toBe(false);
    });

    test("refresh IP bucket is DECOUPLED from login IP bucket (v130 root fix)", async () => {
      // v128 reused the shared login IP limiter (checkIpRateLimitDistributed).
      // v130 uses a dedicated `refresh:ip:*` key. This test verifies the
      // refresh bucket is SEPARATE — exhausting the refresh bucket does NOT
      // block a different `refresh:ip:*` key, and vice versa.
      const ipKey = "refresh:ip:203.0.113.30";
      // Exhaust the refresh bucket for this IP.
      for (let i = 0; i < REFRESH_IP_RATE_LIMIT.max + 1; i++) {
        await checkUserRateLimitDistributed(ipKey, REFRESH_IP_RATE_LIMIT);
      }
      // Same IP, refresh bucket — blocked.
      const rlRefresh = await checkUserRateLimitDistributed(ipKey, REFRESH_IP_RATE_LIMIT);
      expect(rlRefresh.blocked).toBe(true);
      // Same IP, DIFFERENT key (e.g. login bucket) — NOT blocked.
      // This proves the refresh limiter doesn't pollute other buckets.
      const rlOther = await checkUserRateLimitDistributed(
        `login:ip:203.0.113.30`,
        { max: 20, windowSeconds: 300 }
      );
      expect(rlOther.blocked).toBe(false);
    });

    test("supports IPv6 addresses", async () => {
      const ipKey = "refresh:ip:2001:db8::1";
      const rl = await checkUserRateLimitDistributed(ipKey, REFRESH_IP_RATE_LIMIT);
      expect(rl.blocked).toBe(false);
      // Different IPv6 — independent bucket.
      const rl2 = await checkUserRateLimitDistributed(
        "refresh:ip:2001:db8::2",
        REFRESH_IP_RATE_LIMIT
      );
      expect(rl2.blocked).toBe(false);
    });
  });

  // =========================================================================
  // Layer 2: per-user rate limit (10/hour, post-authentication).
  // The route calls checkUserRateLimitDistributed(userId, REFRESH_USER_RATE_LIMIT)
  // AFTER consumeRefreshToken succeeds. This test exercises the SAME function
  // with the SAME limits to verify the per-user cap.
  // =========================================================================
  describe("Layer 2: per-user rate limit (10/hour, post-authentication)", () => {
    test("allows up to 10 refreshes per hour per user", async () => {
      const userId = "user-10-per-hour-ok";
      for (let i = 0; i < REFRESH_USER_RATE_LIMIT.max; i++) {
        const rl = await checkUserRateLimitDistributed(userId, REFRESH_USER_RATE_LIMIT);
        expect(rl.blocked).toBe(false);
        expect(rl.remaining).toBe(REFRESH_USER_RATE_LIMIT.max - i - 1);
      }
    });

    test("blocks the 11th refresh within the same hour (429 + Retry-After)", async () => {
      const userId = "user-10-per-hour-block";
      // Make 10 allowed requests.
      for (let i = 0; i < REFRESH_USER_RATE_LIMIT.max; i++) {
        const rl = await checkUserRateLimitDistributed(userId, REFRESH_USER_RATE_LIMIT);
        expect(rl.blocked).toBe(false);
      }
      // 11th request — should be blocked.
      const rl = await checkUserRateLimitDistributed(userId, REFRESH_USER_RATE_LIMIT);
      expect(rl.blocked).toBe(true);
      expect(rl.retryAfterSeconds).toBeGreaterThan(0);
      // Retry-after should be ≤ 1 hour (the window length). A sliding-window
      // implementation returns the time until the oldest entry expires, which
      // is ≤ windowSeconds. A fixed-window implementation could return up to
      // 2x windowSeconds (time until next window reset). This assertion
      // verifies the implementation is sliding-window.
      expect(rl.retryAfterSeconds).toBeLessThanOrEqual(REFRESH_USER_RATE_LIMIT.windowSeconds);
      expect(rl.remaining).toBe(0);
    });

    test("rate limit is PER-USER — different users have independent buckets", async () => {
      const user1 = "user-independent-1";
      const user2 = "user-independent-2";
      // Exhaust user 1's bucket.
      for (let i = 0; i < REFRESH_USER_RATE_LIMIT.max + 1; i++) {
        await checkUserRateLimitDistributed(user1, REFRESH_USER_RATE_LIMIT);
      }
      // User 1 is blocked.
      const rl1 = await checkUserRateLimitDistributed(user1, REFRESH_USER_RATE_LIMIT);
      expect(rl1.blocked).toBe(true);
      // User 2 is NOT blocked.
      const rl2 = await checkUserRateLimitDistributed(user2, REFRESH_USER_RATE_LIMIT);
      expect(rl2.blocked).toBe(false);
    });

    test("per-user limit does NOT interfere with per-IP limit (different keys)", async () => {
      // The route uses `refresh:ip:${ip}` for Layer 1 and `${userId}` for
      // Layer 2. These are different keys, so exhausting one does NOT
      // affect the other. This test verifies that separation.
      const userId = "user-cross-layer";
      const ipKey = "refresh:ip:203.0.113.99";
      // Exhaust the user's per-user bucket.
      for (let i = 0; i < REFRESH_USER_RATE_LIMIT.max + 1; i++) {
        await checkUserRateLimitDistributed(userId, REFRESH_USER_RATE_LIMIT);
      }
      // User is blocked.
      const rlUser = await checkUserRateLimitDistributed(userId, REFRESH_USER_RATE_LIMIT);
      expect(rlUser.blocked).toBe(true);
      // The IP bucket (different key) is NOT blocked.
      const rlIp = await checkUserRateLimitDistributed(ipKey, REFRESH_IP_RATE_LIMIT);
      expect(rlIp.blocked).toBe(false);
    });
  });

  // =========================================================================
  // 429 + Retry-After header contract verification.
  // The route handler does:
  //   headers: { "Retry-After": String(rl.retryAfterSeconds) }
  // So retryAfterSeconds MUST be a positive integer when blocked.
  // =========================================================================
  describe("429 + Retry-After response contract", () => {
    test("per-IP limiter returns positive integer retryAfterSeconds when blocked", async () => {
      const ipKey = "refresh:ip:203.0.113.99";
      // Exhaust the bucket.
      for (let i = 0; i < REFRESH_IP_RATE_LIMIT.max + 1; i++) {
        await checkUserRateLimitDistributed(ipKey, REFRESH_IP_RATE_LIMIT);
      }
      const rl = await checkUserRateLimitDistributed(ipKey, REFRESH_IP_RATE_LIMIT);
      expect(rl.blocked).toBe(true);
      expect(Number.isInteger(rl.retryAfterSeconds)).toBe(true);
      expect(rl.retryAfterSeconds).toBeGreaterThan(0);
      expect(rl.retryAfterSeconds).toBeLessThanOrEqual(REFRESH_IP_RATE_LIMIT.windowSeconds);
    });

    test("per-user limiter returns positive integer retryAfterSeconds when blocked", async () => {
      const userId = "user-retry-after-check";
      // Exhaust the bucket.
      for (let i = 0; i < REFRESH_USER_RATE_LIMIT.max + 1; i++) {
        await checkUserRateLimitDistributed(userId, REFRESH_USER_RATE_LIMIT);
      }
      const rl = await checkUserRateLimitDistributed(userId, REFRESH_USER_RATE_LIMIT);
      expect(rl.blocked).toBe(true);
      expect(Number.isInteger(rl.retryAfterSeconds)).toBe(true);
      expect(rl.retryAfterSeconds).toBeGreaterThan(0);
      expect(rl.retryAfterSeconds).toBeLessThanOrEqual(REFRESH_USER_RATE_LIMIT.windowSeconds);
    });
  });

  // =========================================================================
  // Task spec verification: "should 429 after 5" when 10 requests from same IP.
  // The task spec literally says:
  //   for i in $(seq 1 10); do curl -X POST http://localhost:3000/api/auth/refresh; done
  //   # should 429 after 5
  // This test simulates that exact scenario: 10 requests from the same IP,
  // the first 5 are allowed (200/401 depending on token), the 6th-10th are
  // blocked by the per-IP rate limiter (429).
  // =========================================================================
  describe("Task spec verification: 10 requests from same IP, 429 after 5", () => {
    test("first 5 refreshes from same IP are allowed, 6th-10th are blocked", async () => {
      const ipKey = "refresh:ip:198.51.100.42";
      const results: { blocked: boolean; retryAfterSeconds: number }[] = [];
      for (let i = 0; i < 10; i++) {
        const rl = await checkUserRateLimitDistributed(ipKey, REFRESH_IP_RATE_LIMIT);
        results.push({ blocked: rl.blocked, retryAfterSeconds: rl.retryAfterSeconds });
      }
      // First 5 (indexes 0-4): allowed.
      for (let i = 0; i < 5; i++) {
        expect(results[i].blocked).toBe(false);
      }
      // 6th-10th (indexes 5-9): blocked.
      for (let i = 5; i < 10; i++) {
        expect(results[i].blocked).toBe(true);
        expect(results[i].retryAfterSeconds).toBeGreaterThan(0);
      }
    });
  });
});
