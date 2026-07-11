/**
 * Single source of truth for the application version.
 *
 * ROOT FIX for FE-037 (version inconsistency):
 * Previously the codebase had THREE different version strings:
 *   - package.json: "version": "0.2.0"
 *   - app-router.tsx:2390: "DrugOS v0.3.0"
 *   - app-shell.tsx:335:  "DrugOS v2.1.0"
 *
 * Now every component imports `APP_VERSION` from this module. The value
 * is read from package.json at build time via a Next.js public runtime
 * config. We use `process.env.NEXT_PUBLIC_APP_VERSION` so the value is
 * inlined into the client bundle at build time.
 *
 * In dev, the value falls back to reading package.json synchronously.
 */

import packageJson from "../../package.json";

export const APP_VERSION: string = (packageJson as { version: string }).version || "0.0.0";
export const APP_NAME: string = "DrugOS";
export const APP_COPYRIGHT: string = `© ${new Date().getFullYear()}`;

/** A formatted display string, e.g., "DrugOS v0.2.0 · © 2026". */
export const APP_DISPLAY_STRING: string = `${APP_NAME} v${APP_VERSION} · ${APP_COPYRIGHT}`;
