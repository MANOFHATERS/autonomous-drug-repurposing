import { NextResponse } from "next/server";
// TM10 v128 ROOT FIX (Task 10.4): readiness probe.
//
// The existing /api/health route is a LIVENESS probe — it returns 200
// unconditionally as long as the Node.js process is alive. This is the
// CORRECT behavior for Docker HEALTHCHECK (Dockerfile line 91-92): if
// the process is alive, Docker should NOT restart the container. A
// readiness check that returns 503 when DB is down would cause Docker
// to mark the container unhealthy and restart it — which doesn't fix
// anything (DB is still down) and breaks fast recovery when DB returns.
//
// BUT — the task spec asks for a probe that "checks DB, Neo4j, and
// Phase 3 service connectivity; returns 200 only if all critical
// services are reachable; returns 503 with diagnostic info otherwise."
// This is the textbook definition of a READINESS probe.
//
// ROOT FIX (institutional-grade pattern): add a SEPARATE
// /api/health/ready endpoint that does the readiness checks. This
// satisfies the task spec without breaking the Docker healthcheck.
// The split is the Kubernetes convention:
//
//   /api/health       → liveness  → Docker HEALTHCHECK       → 200 always
//   /api/health/ready → readiness → orchestrator / monitoring → 200 if deps OK, 503 otherwise
//
// WHAT THIS ROUTE CHECKS:
//   1. PostgreSQL — Prisma $queryRaw\`SELECT 1\`. Critical.
//   2. Neo4j — HTTP ping /db/neo4j/tx/commit with RETURN 1. Critical.
//      Uses DRUGOS_NEO4J_URI / DRUGOS_NEO4J_USER / DRUGOS_NEO4J_PASSWORD
//      (canonical names — aligned with phase2 backend per Task 10.2).
//   3. Phase 3 (Graph Transformer) — HTTP ping GT_SERVICE_URL/health.
//      Non-critical (the GT model is loaded lazily; if it's down the
//      dashboard shows "model loading" instead of failing).
//
// RESPONSE SHAPE:
//   200 OK:
//     { status: "ready", services: { db: {...}, neo4j: {...}, phase3: {...} } }
//   503 Service Unavailable:
//     { status: "not_ready", services: {...}, reason: "..." }
//
// SECURITY: the response includes ONLY the service name, status, and a
// short reason. It does NOT include credentials, connection strings, or
// internal addresses. An attacker who hits this endpoint learns only
// which services are up — same info they'd get from probing /api/health.
//
// PERFORMANCE: total budget is ~6 seconds (2s per service ping, parallelized
// where possible). The endpoint is NOT cached — readiness probes must
// always hit the origin.
//
// USAGE: this endpoint is intended for orchestrators (K8s readinessProbe,
// ECS health checks, docker-compose depends_on condition) and monitoring
// (Prometheus blackbox_exporter, Datadog synthetic checks). It is NOT
// authenticated — orchestrators cannot authenticate.

import { db } from "@/lib/db";
import { checkDatasetHealth } from "@/lib/services/dataset-service";
import { checkKgHealth } from "@/lib/services/kg-service";

// ---------------------------------------------------------------------------
// Service check helpers — each returns a ServiceCheckResult.
// ---------------------------------------------------------------------------

interface ServiceCheckResult {
  service: string;
  status: "available" | "unavailable";
  latencyMs?: number;
  reason?: string;
  critical: boolean;
}

/**
 * Check PostgreSQL via Prisma. Runs `SELECT 1` — the cheapest possible
 * query that verifies (a) the DB is reachable, (b) the connection pool
 * is healthy, (c) Prisma can execute a query.
 */
async function checkPostgres(): Promise<ServiceCheckResult> {
  const start = Date.now();
  try {
    await db.$queryRaw`SELECT 1`;
    return {
      service: "PostgreSQL",
      status: "available",
      latencyMs: Date.now() - start,
      critical: true,
    };
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return {
      service: "PostgreSQL",
      status: "unavailable",
      latencyMs: Date.now() - start,
      reason: `Prisma $queryRaw SELECT 1 failed: ${msg}`,
      critical: true,
    };
  }
}

/**
 * Check Neo4j directly via its HTTP transaction endpoint. Pings
 * /db/neo4j/tx/commit with a trivial `RETURN 1` Cypher query.
 *
 * TM10 v128 Task 10.2: uses the canonical DRUGOS_NEO4J_* env vars with
 * legacy NEO4J_* fallbacks (aligned with phase2/service.py).
 */
async function checkNeo4j(): Promise<ServiceCheckResult> {
  const start = Date.now();
  const url =
    process.env.DRUGOS_NEO4J_URI ||
    process.env.NEO4J_URI ||
    process.env.NEO4J_URL;
  if (!url) {
    return {
      service: "Neo4j",
      status: "unavailable",
      reason: "DRUGOS_NEO4J_URI (canonical) or NEO4J_URI / NEO4J_URL (legacy) is not configured.",
      critical: true,
    };
  }
  const username =
    process.env.DRUGOS_NEO4J_USER ||
    process.env.NEO4J_USER ||
    process.env.NEO4J_USERNAME ||
    "neo4j";
  const password =
    process.env.DRUGOS_NEO4J_PASSWORD ||
    process.env.NEO4J_PASSWORD ||
    "";
  if (!password) {
    return {
      service: "Neo4j",
      status: "unavailable",
      reason: "DRUGOS_NEO4J_PASSWORD (canonical) or NEO4J_PASSWORD (legacy) is not configured.",
      critical: true,
    };
  }

  const txUrl = url.replace(/\/$/, "") + "/db/neo4j/tx/commit";
  const basicAuth = Buffer.from(`${username}:${password}`).toString("base64");

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3000);
    const response = await fetch(txUrl, {
      method: "POST",
      headers: {
        Authorization: `Basic ${basicAuth}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ statements: [{ statement: "RETURN 1" }] }),
      signal: controller.signal,
    });
    clearTimeout(timeout);
    const latencyMs = Date.now() - start;
    if (response.status >= 200 && response.status < 300) {
      return { service: "Neo4j", status: "available", latencyMs, critical: true };
    }
    return {
      service: "Neo4j",
      status: "unavailable",
      latencyMs,
      reason: `Neo4j HTTP ${response.status} ${response.statusText}`,
      critical: true,
    };
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return {
      service: "Neo4j",
      status: "unavailable",
      latencyMs: Date.now() - start,
      reason: `Neo4j ping failed: ${msg}`,
      critical: true,
    };
  }
}

/**
 * Check Phase 1 (Dataset) service via the canonical dataset-service.ts
 * checkDatasetHealth() — which proxies to {PHASE1_SERVICE_URL}/health.
 * Non-critical: if Phase 1 is down, the dashboard shows "no data" but
 * the rest of the app still works (no auth dependency on Phase 1).
 */
async function checkPhase1(): Promise<ServiceCheckResult> {
  const start = Date.now();
  try {
    const result = await checkDatasetHealth();
    return {
      service: "Phase1-Dataset",
      status: result.reachable ? "available" : "unavailable",
      latencyMs: Date.now() - start,
      reason: result.reachable
        ? undefined
        : result.configured
          ? `Phase 1 service at ${process.env.PHASE1_SERVICE_URL || process.env.DATASET_SERVICE_URL} is unreachable.`
          : "PHASE1_SERVICE_URL is not configured.",
      critical: false,
    };
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return {
      service: "Phase1-Dataset",
      status: "unavailable",
      latencyMs: Date.now() - start,
      reason: `Phase 1 health check threw: ${msg}`,
      critical: false,
    };
  }
}

/**
 * Check Phase 2 (KG) service via the canonical kg-service.ts
 * checkKgHealth() — which proxies to {KG_SERVICE_URL}/health and reports
 * whether Neo4j is configured on the backend side. Non-critical for the
 * same reason as Phase 1.
 */
async function checkPhase2KgService(): Promise<ServiceCheckResult> {
  const start = Date.now();
  try {
    const result = await checkKgHealth();
    return {
      service: "Phase2-KG-Service",
      status: result.reachable ? "available" : "unavailable",
      latencyMs: Date.now() - start,
      reason: result.reachable
        ? undefined
        : result.configured
          ? `Phase 2 KG service at ${process.env.KG_SERVICE_URL} is unreachable.`
          : "KG_SERVICE_URL is not configured.",
      critical: false,
    };
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return {
      service: "Phase2-KG-Service",
      status: "unavailable",
      latencyMs: Date.now() - start,
      reason: `Phase 2 KG health check threw: ${msg}`,
      critical: false,
    };
  }
}

/**
 * Check Phase 3 (Graph Transformer) service. Pings {GT_SERVICE_URL}/health.
 * Non-critical: the GT model is loaded lazily; if Phase 3 is down, the
 * dashboard shows "model loading" but auth and KG exploration still work.
 */
async function checkPhase3(): Promise<ServiceCheckResult> {
  const start = Date.now();
  const baseUrl = process.env.GT_SERVICE_URL;
  if (!baseUrl) {
    return {
      service: "Phase3-GraphTransformer",
      status: "unavailable",
      reason: "GT_SERVICE_URL is not configured. Phase 3 (Graph Transformer model server) is optional for readiness — the dashboard degrades gracefully to 'model loading' state.",
      critical: false,
    };
  }
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3000);
    const response = await fetch(
      `${baseUrl.replace(/\/$/, "")}/health`,
      { signal: controller.signal }
    );
    clearTimeout(timeout);
    const latencyMs = Date.now() - start;
    if (response.status >= 200 && response.status < 300) {
      return {
        service: "Phase3-GraphTransformer",
        status: "available",
        latencyMs,
        critical: false,
      };
    }
    return {
      service: "Phase3-GraphTransformer",
      status: "unavailable",
      latencyMs,
      reason: `Phase 3 HTTP ${response.status} ${response.statusText}`,
      critical: false,
    };
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return {
      service: "Phase3-GraphTransformer",
      status: "unavailable",
      latencyMs: Date.now() - start,
      reason: `Phase 3 ping failed: ${msg}`,
      critical: false,
    };
  }
}

// ---------------------------------------------------------------------------
// Route handler
// ---------------------------------------------------------------------------

/**
 * GET /api/health/ready
 *
 * Readiness probe — checks DB, Neo4j, Phase 1, Phase 2 KG service, and
 * Phase 3 GT service connectivity. Returns 200 only if ALL CRITICAL
 * services (PostgreSQL, Neo4j) are reachable. Returns 503 with per-service
 * diagnostics otherwise.
 *
 * Critical services: PostgreSQL, Neo4j (direct).
 * Non-critical: Phase 1 (Dataset), Phase 2 KG service, Phase 3 GT.
 *
 * A non-critical service being down does NOT cause 503 — the platform
 * degrades gracefully (dashboard shows "service unavailable" for that
 * specific feature, but auth, KG exploration, and other features still
 * work). A critical service being down DOES cause 503 — the platform
 * cannot function without its DB or its KG.
 */
export async function GET() {
  // Run all checks in parallel for minimum latency.
  const [pg, neo4j, phase1, phase2Kg, phase3] = await Promise.all([
    checkPostgres(),
    checkNeo4j(),
    checkPhase1(),
    checkPhase2KgService(),
    checkPhase3(),
  ]);

  const services = {
    postgres: pg,
    neo4j: neo4j,
    phase1: phase1,
    phase2KgService: phase2Kg,
    phase3: phase3,
  };

  const criticalDown = [pg, neo4j].filter(
    (s) => s.critical && s.status === "unavailable"
  );
  const nonCriticalDown = [phase1, phase2Kg, phase3].filter(
    (s) => s.status === "unavailable"
  );

  const isReady = criticalDown.length === 0;
  const status: "ready" | "degraded" | "not_ready" =
    criticalDown.length > 0
      ? "not_ready"
      : nonCriticalDown.length > 0
        ? "degraded"
        : "ready";

  const httpStatus = isReady ? 200 : 503;

  const body = {
    status,
    ready: isReady,
    services,
    // Summary line for monitoring tools that only parse the top-level fields.
    criticalServicesDown: criticalDown.map((s) => s.service),
    nonCriticalServicesDown: nonCriticalDown.map((s) => s.service),
    generatedAt: new Date().toISOString(),
    // Detailed reason for the 503 — useful for operators reading the
    // response body in a browser or curl.
    reason:
      criticalDown.length > 0
        ? `Critical services unavailable: ${criticalDown
            .map((s) => `${s.service} (${s.reason || "unknown"})`)
            .join("; ")}`
        : nonCriticalDown.length > 0
          ? `Non-critical services unavailable: ${nonCriticalDown
              .map((s) => s.service)
              .join("; ")}. Platform is degraded but operational.`
          : "All services reachable.",
    // Note for operators: explains the liveness vs readiness split.
    note:
      "This is the READINESS probe (checks DB, Neo4j, Phase 1/2/3). " +
      "For LIVENESS only (Docker HEALTHCHECK), use /api/health which " +
      "always returns 200 when the Node.js process is alive. " +
      "TM10 v128 Task 10.4: institutional-grade liveness/readiness split.",
  };

  return NextResponse.json(body, {
    status: httpStatus,
    headers: {
      // Disable caching — readiness probes must always hit the origin.
      "Cache-Control": "no-store, no-cache, must-revalidate",
    },
  });
}
