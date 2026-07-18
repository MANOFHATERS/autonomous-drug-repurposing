/**
 * BE-066 REAL ROOT FIX (v126) regression test: EVERY TOTP-protected route
 * uses the DISTRIBUTED `recordFailedTotpDistributed`, NOT the sync
 * `recordFailedTotp`.
 *
 * HOSTILE-AUDITOR CONTEXT
 * -----------------------
 * The original BE-066 audit finding said:
 *   "Migrate ALL `recordFailedTotp` callers to `recordFailedTotpDistributed`
 *    (already implemented, just not wired)."
 *
 * The v123 "BE-066 ROOT FIX" only migrated /api/auth/2fa/login-verify.
 * The audit's "all callers" instruction was treated as "the one caller I
 * am looking at right now" — exactly the surface-level fix pattern the
 * user explicitly warned against. /api/auth/2fa/disable and
 * /api/billing/subscription STILL called the sync `recordFailedTotp`,
 * which uses an in-memory Map keyed per Node.js process. On a multi-
 * instance deploy (K8s with N replicas), each instance had its own
 * counter — an attacker could make N × TOTP_MAX_ATTEMPTS attempts before
 * lockout (N=3 → 15 attempts → ~6 min to brute-force TOTP).
 *
 * This source-level regression test scans EVERY TOTP-protected route
 * under frontend/src/app/api and asserts:
 *   1. The route imports `recordFailedTotpDistributed` (NOT just
 *      `recordFailedTotp`).
 *   2. The route's `recordFailedTotp(` call site (if any) is the
 *      distributed one — i.e. the call is `await recordFailedTotpDistributed(`.
 *   3. The route does NOT call the sync `recordFailedTotp(` at runtime.
 *      Comments and docstrings may still mention the sync name (for
 *      historical context) — those are NOT flagged. We distinguish a
 *      call from a reference by requiring the `(` immediately after
 *      the identifier with NO `Distributed` suffix.
 *
 * This catches the "fixed one site, missed two others" pattern that
 * recurred across the v100-v125 audit cycles. If a future developer
 * adds a new TOTP-protected route and forgets to use the distributed
 * version, this test fails.
 */
import * as fs from "fs";
import * as path from "path";

// Routes that verify a TOTP code and therefore must record failed attempts
// via the DISTRIBUTED rate limiter. If you add a new TOTP-protected route,
// add its path here — the test will assert it uses the distributed version.
// Paths are RELATIVE TO THIS TEST FILE (__dirname).
const TOTP_PROTECTED_ROUTES: Array<{ name: string; relPath: string }> = [
  {
    name: "/api/auth/2fa/login-verify",
    relPath:
      "../../auth/2fa/login-verify/route.ts",
  },
  {
    name: "/api/auth/2fa/disable",
    relPath:
      "../../auth/2fa/disable/route.ts",
  },
  {
    name: "/api/billing/subscription",
    relPath:
      "../../billing/subscription/route.ts",
  },
];

function readRouteSource(relPath: string): string {
  const fullPath = path.resolve(__dirname, relPath);
  return fs.readFileSync(fullPath, "utf-8");
}

describe("BE-066 v126: every TOTP-protected route uses recordFailedTotpDistributed", () => {
  for (const route of TOTP_PROTECTED_ROUTES) {
    describe(`${route.name}`, () => {
      const source = readRouteSource(route.relPath);

      test("imports recordFailedTotpDistributed from @/lib/auth/rate-limit", () => {
        expect(source).toMatch(/from\s+["']@\/lib\/auth\/rate-limit["']/);
        // The import list must include `recordFailedTotpDistributed` as an
        // imported identifier (not just a comment mention).
        // We look for it inside the import block. Use [\s\S]*? (non-greedy,
        // multiline-safe) instead of [^}]* — the latter is greedy and
        // over-matches when the import block contains comments with `}`
        // characters, or when there are multiple import blocks. The
        // non-greedy variant matches the SMALLEST block from `import {` to
        // `} from "@/lib/auth/rate-limit"`.
        const importBlockMatch = source.match(
          /import\s*\{[\s\S]*?\}\s*from\s*["']@\/lib\/auth\/rate-limit["']/
        );
        expect(importBlockMatch).not.toBeNull();
        expect(importBlockMatch![0]).toMatch(/\brecordFailedTotpDistributed\b/);
      });

      test("does NOT call the sync `recordFailedTotp(` at runtime (only the distributed version)", () => {
        // Find every occurrence of `recordFailedTotp(` in the source.
        // For each, check whether the immediately preceding chars make it
        // `recordFailedTotpDistributed(` — if so, it's the distributed call
        // (allowed). Otherwise it's a sync call (FORBIDDEN).
        const forbidden = /\brecordFailedTotp\(/g;
        let m: RegExpExecArray | null;
        let violations: string[] = [];
        while ((m = forbidden.exec(source)) !== null) {
          // Check the 11 chars BEFORE the match — if they spell
          // "Distributed", this is actually `recordFailedTotpDistributed(`
          // and the regex just matched the suffix.
          const start = Math.max(0, m.index - 11);
          const prefix = source.slice(start, m.index);
          if (prefix.endsWith("Distributed")) {
            // OK — distributed call.
            continue;
          }
          // Forbidden — sync call at runtime.
          const lineNum = source.slice(0, m.index).split("\n").length;
          violations.push(
            `line ${lineNum}: found sync \`recordFailedTotp(\` call — use \`await recordFailedTotpDistributed(\` instead`
          );
        }
        expect(violations).toEqual([]);
      });

      test("awaits the distributed call (it returns a Promise)", () => {
        // The distributed version returns Promise<{locked, ...}> — calling
        // it without `await` would yield a Promise object, and
        // `afterFail.locked` would be `undefined` (truthy check passes
        // incorrectly). The call MUST be `await recordFailedTotpDistributed(`.
        const callIdx = source.indexOf("recordFailedTotpDistributed(");
        expect(callIdx).toBeGreaterThan(-1);
        // Look at the 30 chars before the call to find `await`. Allow
        // whitespace and comments between `await` and the call.
        const before = source.slice(Math.max(0, callIdx - 60), callIdx);
        expect(before).toMatch(/\bawait\s*$/);
      });
    });
  }
});

describe("BE-066 v126: no OTHER routes under /api call the sync recordFailedTotp", () => {
  // Scan EVERY route.ts under frontend/src/app/api and assert none of them
  // call the sync `recordFailedTotp(`. The three known TOTP routes are
  // tested explicitly above; this catch-all ensures future routes don't
  // silently reintroduce the sync version.
  //
  // We deliberately skip __tests__ directories and only look at route.ts
  // files (the Next.js convention for route handlers).
  // The api root is src/app/api — two levels up from this test file
  // (test file: src/app/api/__tests__/be066-totp-distributed-wired/x.test.ts
  //  api root:  src/app/api).
  const apiRoot = path.resolve(__dirname, "../../");
  const routeFiles: string[] = [];
  function walk(dir: string) {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const e of entries) {
      const full = path.join(dir, e.name);
      if (e.isDirectory()) {
        if (e.name === "__tests__" || e.name === "node_modules") continue;
        walk(full);
      } else if (e.isFile() && e.name === "route.ts") {
        routeFiles.push(full);
      }
    }
  }
  walk(apiRoot);

  test(`scanned ${routeFiles.length} route.ts files under /api`, () => {
    expect(routeFiles.length).toBeGreaterThan(0);
  });

  for (const file of routeFiles) {
    const rel = path.relative(apiRoot, file);
    test(`${rel} does not call sync recordFailedTotp`, () => {
      const src = fs.readFileSync(file, "utf-8");
      // Same check as above — find every `recordFailedTotp(` and verify
      // the preceding chars are "Distributed".
      const forbidden = /\brecordFailedTotp\(/g;
      let m: RegExpExecArray | null;
      let violations: string[] = [];
      while ((m = forbidden.exec(src)) !== null) {
        const start = Math.max(0, m.index - 11);
        const prefix = src.slice(start, m.index);
        if (prefix.endsWith("Distributed")) continue;
        const lineNum = src.slice(0, m.index).split("\n").length;
        violations.push(`line ${lineNum}`);
      }
      expect(violations).toEqual([]);
    });
  }
});
