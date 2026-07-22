/**
 * Single source of truth for the application version.
 *
 * FE-025 ROOT FIX (Teammate 17 — hostile-auditor pass):
 *
 * The previous implementation used BOTH `process.env.NEXT_PUBLIC_APP_VERSION`
 * AND a static `import packageJson from "../../package.json"`. The static
 * import works at BUILD time (webpack inlines the JSON), but it is fragile
 * in standalone Docker deployments because:
 *
 *   1. The Dockerfile copies `.next/standalone`, `.next/static`, and
 *      `public/` — it does NOT copy `package.json` into the standalone
 *      output.
 *   2. Any future refactor that defers the import to runtime (e.g. for
 *      runtime version introspection) would crash the standalone server
 *      with "Cannot find module '../../package.json'".
 *
 * ROOT FIX (this file):
 *   - We read ONLY from `process.env.NEXT_PUBLIC_APP_VERSION`.
 *   - That env var is set at BUILD time by `next.config.ts` via the
 *     `env` field (`NEXT_PUBLIC_APP_VERSION: packageJson.version`).
 *     Next.js's DefinePlugin inlines the literal string into every
 *     client bundle module that references `process.env.NEXT_PUBLIC_APP_VERSION`.
 *   - No static JSON import is needed in this file — version.ts is
 *     robust to cwd changes, standalone Docker builds, and runtime
 *     introspection.
 *   - If the env var is somehow unset (e.g. running the file outside
 *     the Next.js build pipeline, like in a unit test), we fall back
 *     to "0.0.0-unknown" rather than crashing.
 *
 * This is the canonical pattern recommended by the Next.js docs for
 * exposing build-time constants to the client bundle.
 */

const ENV_VERSION: string | undefined = process.env.NEXT_PUBLIC_APP_VERSION;

export const APP_VERSION: string = ENV_VERSION || "0.0.0-unknown";
export const APP_NAME: string = "DrugOS";
export const APP_COPYRIGHT: string = `© ${new Date().getFullYear()}`;

/** A formatted display string, e.g., "DrugOS v0.2.0 · © 2026". */
export const APP_DISPLAY_STRING: string = `${APP_NAME} v${APP_VERSION} · ${APP_COPYRIGHT}`;
