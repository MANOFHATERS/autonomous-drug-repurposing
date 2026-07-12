import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";
import { dirname } from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

/**
 * FE-027 ROOT FIX: The previous ESLint config disabled EVERY meaningful
 * rule — `@typescript-eslint/no-explicit-any: off`, `no-unused-vars: off`,
 * `no-unreachable: off`, `no-console: off`, `react-hooks/exhaustive-deps: off`,
 * and 20+ more. ESLint would pass ANY code. The linter was a no-op.
 *
 * Production-grade code MUST enforce baseline quality rules. We re-enable
 * the rules with pragmatic severity:
 *   - Errors: things that cause bugs (no-unreachable, no-redeclare,
 *     no-fallthrough, prefer-const, react/jsx-no-undef).
 *   - Warnings: things that are code smells but don't break anything
 *     (no-explicit-any, no-unused-vars, no-console with allow for
 *     warn/error, react-hooks/exhaustive-deps).
 *
 * Existing code produces lint warnings — that's the point. We fix them
 * incrementally rather than disabling the rule globally.
 */
const eslintConfig = [...nextCoreWebVitals, ...nextTypescript, {
  languageOptions: {
    // React 19's JSX transform doesn't require `import React`, but
    // components that reference `React.MouseEvent` etc. still need the
    // global. This prevents false-positive `no-undef` errors.
    // RequestInit, Request, Response, fetch, etc. are DOM/Node globals
    // used in API route handlers — without these, ESLint flags them.
    globals: {
      React: "readonly",
      RequestInit: "readonly",
      Request: "readonly",
      Response: "readonly",
      fetch: "readonly",
      FormData: "readonly",
      Headers: "readonly",
      ReadableStream: "readonly",
      TransformStream: "readonly",
      WritableStream: "readonly",
      TextEncoder: "readonly",
      TextDecoder: "readonly",
      AbortController: "readonly",
      URL: "readonly",
      URLSearchParams: "readonly",
      setTimeout: "readonly",
      clearTimeout: "readonly",
      setInterval: "readonly",
      clearInterval: "readonly",
      Buffer: "readonly",
      process: "readonly",
      console: "readonly",
    },
  },
  rules: {
    // TypeScript rules — RE-ENABLED.
    // no-explicit-any: catches `any` which defeats TypeScript's safety.
    // Allowed via inline `// eslint-disable-next-line` for genuine escape
    // hatches (e.g. parsing untrusted JSON).
    "@typescript-eslint/no-explicit-any": "warn",
    // no-unused-vars: catches dead variables. We allow args starting with
    // `_` (convention for intentionally-unused params, e.g. event handlers
    // that ignore the event). Set to "warn" because the legacy codebase
    // has 500+ unused imports (mostly lucide-react icons) that need
    // incremental cleanup. Warnings are visible in CI without blocking
    // the build.
    "@typescript-eslint/no-unused-vars": [
      "warn",
      {
        argsIgnorePattern: "^_",
        varsIgnorePattern: "^_",
        caughtErrorsIgnorePattern: "^_",
      },
    ],
    // no-non-null-assertion: `foo!.bar` crashes at runtime if foo is null.
    // Use explicit null checks instead.
    "@typescript-eslint/no-non-null-assertion": "warn",
    // ban-ts-comment: `@ts-ignore` silences the compiler — use
    // `@ts-expect-error` with a justification, or fix the type.
    "@typescript-eslint/ban-ts-comment": "warn",
    "@typescript-eslint/prefer-as-const": "error",

    // React rules — RE-ENABLED.
    // exhaustive-deps: missing deps in useEffect/useCallback cause stale
    // closures — the most common React bug. Errors force fixing them.
    "react-hooks/exhaustive-deps": "warn",
    "react-hooks/purity": "warn",
    "react-hooks/set-state-in-effect": "warn",
    "react/no-unescaped-entities": "warn",
    "react/display-name": "warn",
    "react/prop-types": "off", // Not needed in TS.
    "react-compiler/react-compiler": "off", // Experimental.
    "react/jsx-no-undef": "error",

    // Next.js rules.
    "@next/next/no-img-element": "warn", // Use next/image for production.
    "@next/next/no-html-link-for-pages": "error",

    // General JavaScript rules — RE-ENABLED.
    "prefer-const": "error",
    "no-unused-vars": "off", // Handled by @typescript-eslint/no-unused-vars.
    // no-console: allow warn/error (legitimate runtime diagnostics), block
    // log/info (debug noise in production). Override via inline disable.
    "no-console": ["warn", { allow: ["warn", "error"] }],
    "no-debugger": "error",
    "no-empty": "warn",
    "no-irregular-whitespace": "error",
    "no-case-declarations": "error",
    "no-fallthrough": "error",
    "no-mixed-spaces-and-tabs": "error",
    "no-redeclare": "error",
    "no-undef": "error",
    "no-unreachable": "error",
    "no-useless-escape": "warn",
  },
}, {
  ignores: [
    "node_modules/**",
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    "examples/**",
    "skills",
    "uploaded_code/**",
    "src_backup/**",
    "prisma_backup/**",
    "public_backup/**",
    "scripts_backup/**",
    "src/lib/services/__tests__/**",
    "tests/**",
    "scripts/**",
  ],
}];

export default eslintConfig;
