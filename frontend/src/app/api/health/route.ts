import { NextResponse } from "next/server";

/**
 * GET /api/health
 *
 * BE-003 ROOT FIX (v115, CRITICAL): the Dockerfile HEALTHCHECK directive
 * (frontend/Dockerfile line 91-92) was calling
 *   curl -fsS http://localhost:3000/api/health
 * but no such route existed. The Docker daemon's healthcheck therefore
 * returned 404 on every probe, marking the frontend container as
 * "unhealthy". Orchestrators (ECS, K8s, docker-compose `depends_on:
 * condition: service_healthy`) then restarted the container in a loop,
 * and the V1 dashboard never stabilized.
 *
 * The two options identified in the audit were:
 *   (a) Create this route — a TRUE liveness probe that returns 200 OK
 *       with no auth check. The probe verifies that the Next.js server
 *       process is alive and accepting HTTP connections.
 *   (b) Repoint the Dockerfile healthcheck at `/` (the homepage).
 *       But the homepage is a full React render — a slow render under
 *       load could falsely mark the container as unhealthy even though
 *       the API is fine.
 *
 * We choose (a) because a true liveness endpoint should be CHEAP: a
 * minimal JSON body, no DB query, no auth, no upstream calls. This
 * matches the Kubernetes / Docker convention of /healthz or /api/health
 * returning 200 with `{ "status": "ok" }`.
 *
 * TM10 v128 ROOT FIX (Task 10.4): this route is intentionally LIVENESS
 * ONLY. A separate /api/health/ready endpoint handles READINESS checks
 * (PostgreSQL, Neo4j, Phase 1/2/3 service connectivity). The split is
 * the institutional-grade pattern:
 *   - LIVENESS (/api/health) = "is the process alive?" → 200 always.
 *     Docker HEALTHCHECK uses this. If it returned 503 when the DB was
 *     down, Docker would restart the container in a loop — but restarting
 *     doesn't fix a DB outage, and the restart loop prevents fast recovery
 *     when the DB comes back.
 *   - READINESS (/api/health/ready) = "are all deps reachable?" → 200 or 503.
 *     Orchestrators (K8s readinessProbe) and monitoring (Prometheus,
 *     Datadog) use this. A 503 takes the pod out of the load balancer
 *     without restarting it.
 *
 * WHAT THIS ROUTE IS NOT:
 *   - It is NOT a readiness probe. That is /api/health/ready (NEW, TM10 v128).
 *   - It is NOT a metrics endpoint. Metrics are exposed by the
 *     monitoring layer (Prometheus / OpenTelemetry) on a different path.
 *   - It is NOT authenticated. Auth would defeat the purpose — the
 *     Docker daemon cannot authenticate.
 *
 * PERFORMANCE: the route returns in <1ms (no DB, no I/O). It is safe
 * to hit every 30s (the Dockerfile healthcheck interval) without
 * load concerns.
 *
 * SECURITY: the response contains NO sensitive information — only a
 * status string and a timestamp. An attacker who can reach this
 * endpoint learns nothing except that the server is up (which they
 * could learn by hitting `/` anyway).
 */
export async function GET() {
  return NextResponse.json(
    {
      status: "ok",
      service: "drugos-frontend",
      timestamp: new Date().toISOString(),
    },
    {
      status: 200,
      headers: {
        // Disable caching — health probes should always hit the origin.
        "Cache-Control": "no-store, no-cache, must-revalidate",
      },
    }
  );
}
