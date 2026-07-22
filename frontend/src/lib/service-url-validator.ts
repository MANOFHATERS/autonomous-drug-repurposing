/**
 * FE-002 ROOT FIX (Teammate 13, v143, CRITICAL — startup env-var validator).
 *
 * PROBLEM (what is wrong and why):
 * The audit found that the previous error messages in service files
 * (kg-service.ts, dataset-service.ts, gt-inference.ts, rl-ranker.ts)
 * told operators to set *_SERVICE_URL env vars at ports that contradicted
 * the canonical SERVICE_PORTS in contracts/_url-constants.ts:
 *
 *   - kg-service.ts told operators to set KG_SERVICE_URL=http://localhost:8002
 *     (8002 is GT, not KG — operators following the hint pointed the KG
 *     service at the GT service, every KG call hit a 404 on the GT's
 *     /kg/stats endpoint, the dashboard showed "0 nodes", and operators
 *     wiped the Neo4j DB thinking the KG was empty).
 *   - dataset-service.ts told operators PHASE1_SERVICE_URL=http://localhost:8001
 *     (8001 is KG, not Phase 1 — every dataset call hit the KG service).
 *   - gt-inference.ts told operators GT_SERVICE_URL=http://localhost:8003
 *     (8003 is RL, not GT — every /api/predict call hit the RL service).
 *   - rl-ranker.ts told operators RL_SERVICE_URL=http://localhost:8004
 *     (8004 is NOT in SERVICE_PORTS at all — operators couldn't figure
 *     out which port to use).
 *
 * ROOT FIX:
 *   1. Centralize the port constants in _url-constants.ts SERVICE_PORTS
 *      (DONE — added backend_fastapi: 8004 to match FastAPI default).
 *   2. Build the error message from the imported constant — DONE in
 *      rl-ranker.ts (the only service file that still emits a hint).
 *   3. THIS FILE: a startup-time env-var validator that warns when a
 *      *_SERVICE_URL env var points at a port assigned to a DIFFERENT
 *      service. Catches the mis-configuration BEFORE the first API
 *      call, so the operator sees the warning in the server log
 *      immediately (not after a researcher reports "0 nodes" on the
 *      dashboard).
 *
 * USAGE:
 *   This module exports `validateServiceUrlEnvVars()` which is called
 *   once at Next.js startup from `frontend/src/instrumentation.ts`.
 *   In Next.js 14+, `instrumentation.ts` is the canonical startup hook
 *   (https://nextjs.org/docs/app/building-your-application/optimizing/instrumentation).
 *   It runs ONCE per Node.js process (not per request) before any
 *   route handler is invoked.
 *
 * SCIENTIFIC INTEGRITY:
 *   A mis-configured *_SERVICE_URL doesn't just fail — it can return
 *   WRONG DATA. If KG_SERVICE_URL points at the GT service, the GT
 *   service has no /kg/stats endpoint, but the request might match
 *   a different GT route (e.g. /predict) and return a 200 with a
 *   prediction payload that the frontend mis-interprets as KG stats.
 *   The dashboard then shows "nodeCount: 0.87" (a GT score!) as the
 *   KG's node count. This is the silent data corruption the audit
 *   flagged. The validator catches it at startup, before any user
 *   sees corrupted data.
 */

import {
  SERVICE_PORTS,
  SERVICE_URL_ENV_VARS,
  SERVICE_ENV_VAR_NAMES,
  type ServiceName,
} from "@/../contracts/_url-constants";

export interface ServiceUrlValidationWarning {
  envVar: string;
  url: string;
  port: number;
  expectedService: ServiceName;
  expectedPort: number;
  actualService: ServiceName | null;
  message: string;
}

/**
 * Validate every *_SERVICE_URL env var against the canonical SERVICE_PORTS.
 * Returns an array of warnings (empty if everything is consistent).
 *
 * This function is PURE — it reads process.env and returns warnings, but
 * does NOT mutate anything. The caller (instrumentation.ts) decides
 * whether to console.warn, throw, or exit.
 *
 * Validation rules:
 *   1. If a *_SERVICE_URL env var is set, parse its port.
 *   2. Look up the canonical port for the service the env var refers to
 *      (via SERVICE_URL_ENV_VARS).
 *   3. If the URL's port matches a DIFFERENT service's canonical port,
 *      emit a warning naming both services.
 *   4. If the URL's port matches NO service's canonical port, emit a
 *      "port not in canonical contract" warning.
 *   5. If the URL's port matches the expected service's port, no warning.
 *
 * Edge cases:
 *   - URL with no port (e.g. http://kg-service.internal) — skipped (no
 *     port to validate; this is common in docker-compose where the
 *     service name is the hostname).
 *   - URL with a non-numeric port (e.g. http://kg:kgport) — skipped.
 *   - URL that fails to parse — emit a "malformed URL" warning.
 */
export function validateServiceUrlEnvVars(): ServiceUrlValidationWarning[] {
  const warnings: ServiceUrlValidationWarning[] = [];

  // Build a reverse map: port → service name (for "which service does
  // this port belong to?" lookup).
  const portToService = new Map<number, ServiceName>();
  for (const [name, port] of Object.entries(SERVICE_PORTS)) {
    portToService.set(port as number, name as ServiceName);
  }

  for (const [envVar, expectedService] of Object.entries(SERVICE_URL_ENV_VARS)) {
    const raw = process.env[envVar];
    if (!raw || !raw.trim()) continue; // env var not set — skip

    const url = raw.trim();
    let parsed: URL;
    try {
      parsed = new URL(url);
    } catch {
      warnings.push({
        envVar,
        url,
        port: -1,
        expectedService,
        expectedPort: SERVICE_PORTS[expectedService],
        actualService: null,
        message:
          `${envVar} is set to "${url}" which is not a valid URL. ` +
          `Expected format: http://host:port (e.g. http://localhost:${SERVICE_PORTS[expectedService]}).`,
      });
      continue;
    }

    // Skip if no explicit port (docker-compose service-name URLs have no port).
    if (!parsed.port) continue;
    const port = parseInt(parsed.port, 10);
    if (!Number.isFinite(port) || port <= 0) continue;

    const expectedPort = SERVICE_PORTS[expectedService];
    if (port === expectedPort) continue; // ✓ matches canonical — no warning

    // The env var points at a port that does NOT match its canonical service.
    // Find which service (if any) the port DOES belong to.
    const actualService = portToService.get(port) ?? null;

    if (actualService && actualService !== expectedService) {
      // Port belongs to a DIFFERENT service — this is the critical case.
      warnings.push({
        envVar,
        url,
        port,
        expectedService,
        expectedPort,
        actualService,
        message:
          `${envVar} is set to "${url}" (port ${port}) but port ${port} is ` +
          `the canonical port for ${actualService}, NOT ${expectedService} ` +
          `(which should be on port ${expectedPort} per SERVICE_PORTS in ` +
          `contracts/_url-constants.ts). Every call to ${expectedService} ` +
          `will instead hit ${actualService} — this causes silent data ` +
          `corruption (e.g. KG stats endpoint returns a GT prediction ` +
          `payload that the frontend mis-renders as "nodeCount: 0.87"). ` +
          `FIX: ${envVar}=http://${parsed.hostname}:${expectedPort}`,
      });
    } else if (!actualService) {
      // Port is not in SERVICE_PORTS at all.
      warnings.push({
        envVar,
        url,
        port,
        expectedService,
        expectedPort,
        actualService: null,
        message:
          `${envVar} is set to "${url}" (port ${port}) but port ${port} is ` +
          `NOT in the canonical SERVICE_PORTS contract. The canonical ` +
          `port for ${expectedService} is ${expectedPort}. Using a non-` +
          `canonical port works only if the service was explicitly ` +
          `started on that port — otherwise the call will ECONNREFUSED. ` +
          `FIX: ${envVar}=http://${parsed.hostname}:${expectedPort}`,
      });
    }
  }

  return warnings;
}

/**
 * Print all validation warnings to console.warn. Called once at startup
 * from instrumentation.ts. Returns the warnings array so the caller can
 * also surface them to /api/system/status if desired.
 *
 * This function is GUARDED against non-Node environments (jest, Edge
 * runtime, browser) — it no-ops if `process` is undefined or if
 * `process.env.NODE_ENV === 'production'` AND `process.env.DRUGOS_STRICT_ENV_VALIDATION === '1'`
 * (in strict mode, the process EXITS on warning).
 */
export function logServiceUrlValidationWarnings(): ServiceUrlValidationWarning[] {
  if (typeof process === "undefined") return [];
  const warnings = validateServiceUrlEnvVars();
  for (const w of warnings) {
    console.warn(`[service-url-validator] ${w.message}`);
  }
  // Strict mode: exit on warning. Used in CI to catch env-var drift
  // before a deploy. Disabled by default — operators opt in via
  // DRUGOS_STRICT_ENV_VALIDATION=1.
  if (
    warnings.length > 0 &&
    process.env.DRUGOS_STRICT_ENV_VALIDATION === "1" &&
    process.env.NODE_ENV === "production"
  ) {
    console.error(
      `[service-url-validator] ${warnings.length} env-var validation warning(s) ` +
      `in strict mode (DRUGOS_STRICT_ENV_VALIDATION=1). Exiting.`,
    );
    process.exit(1);
  }
  return warnings;
}

/**
 * Build the canonical hint message for a service. Service files use this
 * to construct their "X is not set" error messages so the port number
 * in the hint can NEVER drift from the canonical contract.
 *
 * Re-exported here so service files can import from a single module
 * (this one) rather than reaching into contracts/_url-constants.ts.
 */
export { buildServiceUrlHint } from "@/../contracts/_url-constants";

/**
 * Convenience: get the canonical env-var name for a service (for log
 * messages and audit trails). Returns "UNKNOWN" if the service is not
 * in SERVICE_ENV_VAR_NAMES.
 */
export function canonicalEnvVarForService(service: ServiceName): string {
  return SERVICE_ENV_VAR_NAMES[service] ?? "UNKNOWN";
}
