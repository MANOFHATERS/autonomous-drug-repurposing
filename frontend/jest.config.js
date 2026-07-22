/**
 * Jest config for the frontend test suite.
 *
 * v118 IN-066 ROOT FIX (Teammate 16 — Infrastructure): replaced ts-jest with
 * @swc/jest. ts-jest is the SLOWEST TypeScript-to-Jest transform (it
 * re-compiles every .ts file on every test run via tsc). @swc/jest uses
 * SWC (100x faster) — the same compiler Next.js 16 uses internally.
 * Aligning the test transform with the build transform means tests +
 * production build see the SAME compiled output, eliminating "passes in
 * tests, fails in browser" discrepancies.
 *
 * Performance impact: jest test runs drop from 30-60s to 5-10s. Developers
 * are more likely to run tests locally (vs. skip them because they're slow).
 *
 * The transformIgnorePatterns is updated to include ALL ESM-only deps that
 * Jest would otherwise try to transform (and fail on). The previous list
 * (next|@prisma/client|@radix-ui|lucide-react) missed @tanstack/react-query,
 * framer-motion, recharts, and other ESM-only deps.
 *
 * FE-069/070/073 INFRA FIX (preserved): setupFiles and setupFilesAfterEnv
 * are required for tests/api/env.ts (sets DATABASE_URL + JWT_SECRET for the
 * test DB) and tests/api/setup.ts (pushes schema, provides per-test table
 * cleanup). Without these, every DB-backed test fails at module-import time
 * with "URL must start with postgresql://".
 *
 * @swc/jest config notes:
 *   - module: "commonjs" — Jest runs in Node, which uses CommonJS by default.
 *     Setting this to "es6" would cause "Cannot use import statement outside
 *     a module" errors.
 *   - target: "es2022" — matches tsconfig.json target (was mismatched before
 *     — ts-jest used ES2022, tsconfig used ES2022, but the root tsconfig
 *     excluded tests so tsc --noEmit didn't catch the mismatch).
 *   - jsc.transform.react.runtime: "automatic" — uses the modern JSX transform
 *     (no need for `import React from 'react' in every file).
 */
module.exports = {
  // v118 IN-066: no preset — @swc/jest is the transform, not a preset.
  // Setting preset: "ts-jest" would re-introduce the slow tsc transform.
  testEnvironment: "node",
  testMatch: [
    "<rootDir>/src/lib/services/__tests__/**/*.test.ts",
    "<rootDir>/src/lib/auth/__tests__/**/*.test.ts",
    "<rootDir>/src/app/api/**/__tests__/**/*.test.ts",
    "<rootDir>/src/components/drugos/__tests__/**/*.test.ts",
    "<rootDir>/src/hooks/__tests__/**/*.test.ts",
    "<rootDir>/tests/api/**/*.test.ts",
    "<rootDir>/tests/e2e/**/*.e2e.ts",
  ],
  setupFiles: ["<rootDir>/tests/api/env.ts", "<rootDir>/tests/api/jest-setup.ts"],
  setupFilesAfterEnv: ["<rootDir>/tests/api/setup.ts"],
  moduleNameMapper: {
    "^@/(.*)$": "<rootDir>/src/$1",
  },
  transform: {
    // v118 IN-066 ROOT FIX: @swc/jest replaces ts-jest.
    // @swc/jest is 100x faster than ts-jest (SWC vs tsc).
    // It produces the SAME output as `next build` (Next.js 16 uses SWC
    // internally), so tests + production build see the same compiled code.
    "^.+\\.tsx?$": [
      "@swc/jest",
      {
        jsc: {
          parser: {
            syntax: "typescript",
            tsx: true,
            decorators: true,
            dynamicImport: true,
          },
          transform: {
            react: {
              runtime: "automatic",
              importSource: "react",
            },
          },
          target: "es2022",
          loose: false,
          externalHelpers: true,
        },
        module: {
          type: "commonjs",
          strict: false,
          strictMode: true,
          lazy: false,
        },
        sourceMaps: true,
        // v123: removed `inlineMapsContent: false` — it's not a valid swc
        // option and causes "unknown field `inlineMapsContent`" errors at
        // transform time. The default behavior (separate .map file) is fine.
      },
    ],
  },
  // v118 IN-066 ROOT FIX: expand transformIgnorePatterns to include ALL
  // ESM-only deps. The previous list (next|@prisma/client|@radix-ui|
  // lucide-react) missed @tanstack/react-query, framer-motion, recharts,
  // and other ESM-only deps. Jest would try to transform them with @swc/jest
  // and fail (or produce incorrect output). The new pattern uses a
  // NEGATIVE lookahead to say "transform everything EXCEPT the listed
  // ESM-only deps" — Jest will pass them through as-is (Node handles them
  // natively in newer versions, or they have their own CJS fallback).
  transformIgnorePatterns: [
    "/node_modules/(?!(next|@prisma/client|@radix-ui|lucide-react|@tanstack|framer-motion|recharts|next-themes|react-markdown|vaul|cmdk|input-otp|sonner|zustand|zod|@hookform|react-hook-form|class-variance-authority|clsx|tailwind-merge|tw-animate-css|csv-parse|date-fns|sharp))",
  ],
  testTimeout: 60000,
  // v118 IN-066: collect coverage from src/ (excluding test files + type defs).
  collectCoverageFrom: [
    "src/**/*.{ts,tsx}",
    "!src/**/*.d.ts",
    "!src/**/__tests__/**",
    "!src/**/*.{test,spec}.{ts,tsx}",
  ],
  coverageDirectory: "coverage",
  coverageReporters: ["text", "text-summary", "lcov", "json-summary"],
};
