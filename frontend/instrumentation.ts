/**
 * FE-002 ROOT FIX (Teammate 13, v143): Next.js instrumentation hook.
 *
 * In Next.js 14+, `instrumentation.ts` at the project root is the
 * canonical startup hook. It runs ONCE per Node.js process, BEFORE any
 * route handler is invoked. This is the correct place to:
 *   - Validate *_SERVICE_URL env vars against canonical SERVICE_PORTS.
 *   - Initialize Sentry / OpenTelemetry.
 *   - Warm up the Prisma connection pool.
 *
 * We use it to run `logServiceUrlValidationWarnings()` at startup so the
 * operator sees the warning in the server log IMMEDIATELY (not after a
 * researcher reports "0 nodes" on the dashboard, by which point the
 * operator may have wiped the Neo4j DB thinking the KG was empty —
 * exactly the failure mode the audit flagged).
 *
 * Reference: https://nextjs.org/docs/app/building-your-application/optimizing/instrumentation
 */

export async function register() {
  // Only run in the Node.js runtime (not Edge). The validator uses
  // process.env which is available in both, but the warnings are only
  // actionable in the long-lived Node.js server process.
  if (process.env.NEXT_RUNTIME === "nodejs") {
    const { logServiceUrlValidationWarnings } = await import("./src/lib/service-url-validator");
    const warnings = logServiceUrlValidationWarnings();
    if (warnings.length > 0) {
      console.warn(
        `[instrumentation] ${warnings.length} service URL validation warning(s) ` +
          `emitted at startup. Search the log for "[service-url-validator]" to see them all. ` +
          `Set DRUGOS_STRICT_ENV_VALIDATION=1 in production to exit on warning.`,
      );
    } else {
      console.info(
        "[instrumentation] service URL env vars validated — no canonical-port mismatches detected.",
      );
    }
  }
}
